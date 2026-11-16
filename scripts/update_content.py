#!/usr/bin/env python3
"""Hourly refresh: regenerates the fraud alerts, AI/security news and Claude
updates sections of the bulletin-data JSON block using the Claude API with
web search. Leaves everything else in that block untouched. Skips cleanly
(no error) if ANTHROPIC_API_KEY isn't set, or if the model's output doesn't
pass validation, so a bad or missing key never breaks the live page.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

INDEX_PATH = "index.html"
MODEL = "claude-haiku-5-5"
API_URL = "https://api.anthropic.com/v1/messages"
BLOCK_RE = re.compile(
    r'(<script id="bulletin-data" type="application/json">\n)(.*?)(\n</script>)',
    re.S,
)

SCHEMA_NOTE = """Respond with ONLY a single JSON object, no markdown fences, no commentary
before or after it. Shape exactly:
{
 "fraud": [{"when": "15 Nov", "title": "...", "tag": "hot", "body": "..."}, ...],
 "ai": [{"when": "15 Nov", "title": "...", "tag": "", "body": "..."}, ...],
 "claude": [{"when": "15 Nov", "title": "...", "tag": "new", "body": "..."}, ...]
}
Rules:
- "fraud": 5-7 current UK-relevant fraud/scam alerts (bank/payment scams, phishing, deepfakes,
  impersonation). "tag" is "hot" for the 1-2 most urgent/current items, else "".
- "ai": 4-6 current AI & cyber-security news items relevant to an office worker. "tag" is "hot"
  for the most urgent item(s), else "".
- "claude": 4-6 recent Claude/Anthropic product updates or releases. "tag" is "new" for a
  brand-new feature/release (last ~2 weeks), "rel" if it's specifically relevant to a UK
  manufacturing/engineering business using Microsoft 365, else "".
- "when" is a short date like "15 Nov" or a short label like "This wk"/"Today".
- "title" under 45 characters. "body" one short sentence, under 100 characters, plain English.
- Prefer genuinely current items (use web search); do not invent facts or fake sources.
- No markdown, no bullet points, no extra keys, no trailing commentary.
"""


def build_prompt(today_label):
    return (
        f"Today is {today_label}, UK time. You are drafting content for a UK office's "
        "digital signage 'Info Bulletin' screen (fraud alerts, AI & cyber security news, "
        "Claude updates). Use web search to find current, real items for each section.\n\n"
        + SCHEMA_NOTE
    )


def call_claude(api_key, prompt):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 4096,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def extract_text(response):
    parts = [b["text"] for b in response.get("content", []) if b.get("type") == "text"]
    return "\n".join(parts).strip()


def strip_fences(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    return text.strip()


def validate(data):
    for key in ("fraud", "ai", "claude"):
        items = data.get(key)
        if not isinstance(items, list) or not items:
            return False
        for item in items:
            if not isinstance(item, dict):
                return False
            if not all(k in item for k in ("when", "title", "tag", "body")):
                return False
            if not isinstance(item["title"], str) or len(item["title"]) > 70:
                return False
            if not isinstance(item["body"], str) or len(item["body"]) > 140:
                return False
    return True


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ANTHROPIC_API_KEY not set, skipping content refresh")
        return

    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    m = BLOCK_RE.search(html)
    if not m:
        print("bulletin-data block not found", file=sys.stderr)
        sys.exit(1)

    bulletin = json.loads(m.group(2))

    now = datetime.now(ZoneInfo("Europe/London"))
    today_label = now.strftime("%A %d %B %Y")

    try:
        response = call_claude(api_key, build_prompt(today_label))
        text = strip_fences(extract_text(response))
        data = json.loads(text)
        if not validate(data):
            print("warning: model output failed validation, leaving content unchanged", file=sys.stderr)
            return
    except Exception as exc:
        print(f"warning: could not refresh content: {exc}", file=sys.stderr)
        return

    bulletin["fraud"] = data["fraud"]
    bulletin["ai"] = data["ai"]
    bulletin["claude"] = data["claude"]

    new_json = json.dumps(bulletin, indent=1, ensure_ascii=False)
    html = html[: m.start()] + m.group(1) + new_json + m.group(3) + html[m.end():]

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print("content refreshed")


if __name__ == "__main__":
    main()
