#!/usr/bin/env python3
"""Hourly refresh for index.html: updates the 'updated' timestamp and the
live Claude row in the bulletin-data JSON block from status.claude.com.
Everything else in that block (fraud alerts, AI/security news, Claude
updates, ticker, other service rows) is left untouched for manual editing.
"""
import json
import re
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

INDEX_PATH = "index.html"
BLOCK_RE = re.compile(
    r'(<script id="bulletin-data" type="application/json">\n)(.*?)(\n</script>)',
    re.S,
)


def fetch_claude_status():
    req = urllib.request.Request(
        "https://status.claude.com/api/v2/summary.json",
        headers={"User-Agent": "Rototherm-Info-Bulletin-Bot/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def claude_row(data):
    indicator = data.get("status", {}).get("indicator", "none")
    if indicator == "none":
        return "ok", "Normal", "No active issues reported"
    incidents = data.get("incidents", [])
    note = incidents[0]["name"] if incidents else data["status"].get("description", "Degraded")
    if indicator == "minor":
        return "minor", "Degraded", note
    return "major", "Outage", note


def main():
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    m = BLOCK_RE.search(html)
    if not m:
        print("bulletin-data block not found", file=sys.stderr)
        sys.exit(1)

    bulletin = json.loads(m.group(2))

    now = datetime.now(ZoneInfo("Europe/London"))
    bulletin["updated"] = now.strftime("%a %d %b %Y, %H:%M %Z")

    try:
        status = fetch_claude_status()
        state, label, note = claude_row(status)
        for svc in bulletin["services"]:
            if svc["name"] == "Claude":
                svc["state"], svc["label"], svc["note"] = state, label, note
                break
        bulletin["statusSource"] = "Live: status.claude.com (Claude) · other rows set manually"
    except Exception as exc:
        print(f"warning: could not refresh Claude status: {exc}", file=sys.stderr)

    new_json = json.dumps(bulletin, indent=1, ensure_ascii=False)
    html = html[: m.start()] + m.group(1) + new_json + m.group(3) + html[m.end():]

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
