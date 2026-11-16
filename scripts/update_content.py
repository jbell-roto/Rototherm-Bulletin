#!/usr/bin/env python3
"""Hourly refresh: regenerates the fraud alerts, AI/security news and Claude
updates sections of the bulletin-data JSON block using the Claude API with
web search. Leaves everything else in that block untouched. Skips cleanly
(no error) if ANTHROPIC_API_KEY isn't set, and each of the three sections is
fetched and validated independently so one bad/truncated response can't
block the other two or break the live page.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

INDEX_PATH = "index.html"
MODEL = "claude-haiku-5-5"
API_URL = "https://api.anthropic.com/v1/messages"
MAX_TOKENS = 8192
BLOCK_RE = re.compile(
    r'(<script id="bulletin-data" type="application/json">\n)(.*?)(\n</script>)',
    re.S,
)

SECTIONS = {
    "fraud": {
        "count": "5-7",
        "tag_rule": '"tag" is "hot" for the 1-2 most urgent/current items, else "".',
        "topic": (
            "current UK-relevant fraud/scam alerts: bank and payment scams, phishing, "
            "deepfakes, impersonation scams, texts/emails pretending to be banks, HMRC, "
            "couriers or well-known companies"
        ),
    },
    "ai": {
        "count": "4-6",
        "tag_rule": '"tag" is "hot" for the most urgent item(s), else "".',
        "topic": "current AI & cyber-security news relevant to an ordinary office worker",
    },
    "claude": {
        "count": "4-6",
        "tag_rule": (
            '"tag" is "new" for a brand-new feature/release (last ~2 weeks), "rel" if it is '
            'specifically relevant to a UK manufacturing/engineering business using '
            'Microsoft 365, else "".'
        ),
        "topic": "recent Claude / Anthropic product updates or releases",
    },
}

ITEM_SHAPE = '{"when": "15 Nov", "title": "...", "tag": "...", "body": "..."}'


def build_prompt(section, today_label):
    cfg = SECTIONS[section]
    return (
        f"Today is {today_label}, UK time. You are drafting ONE section of a UK office's "
        "digital signage 'Info Bulletin' screen. Use web search to find a handful of "
        f"genuinely current, real items about: {cfg['topic']}.\n\n"
        "Respond with ONLY a single JSON array, no markdown fences, no commentary before or "
        f"after it. Each element: {ITEM_SHAPE}\n"
        f"- {cfg['count']} items.\n"
        f"- {cfg['tag_rule']}\n"
        '- "when" is a short date like "15 Nov" or a short label like "This wk"/"Today".\n'
        '- "title" under 45 characters. "body" one short sentence, under 100 characters, '
        "plain English.\n"
        "- Do not invent facts or fake sources. Finish the JSON completely; keep items short "
        "enough that the whole array fits easily within the response budget.\n"
        "- No markdown, no bullet points, no extra keys, no trailing commentary after the "
        "array."
    )


def call_claude(api_key, prompt):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
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
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from None


def extract_text(response):
    if response.get("stop_reason") == "max_tokens":
        raise RuntimeError("response truncated (hit max_tokens)")
    parts = [b["text"] for b in response.get("content", []) if b.get("type") == "text"]
    return "\n".join(parts).strip()


def strip_fences(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    return text.strip()


def validate_items(items):
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


def fetch_section(api_key, section, today_label):
    try:
        response = call_claude(api_key, build_prompt(section, today_label))
        text = strip_fences(extract_text(response))
        items = json.loads(text)
        if not validate_items(items):
            print(f"warning[{section}]: output failed validation, leaving unchanged", file=sys.stderr)
            return None
        return items
    except Exception as exc:
        print(f"warning[{section}]: could not refresh: {exc}", file=sys.stderr)
        return None


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

    changed = False
    for section in SECTIONS:
        items = fetch_section(api_key, section, today_label)
        if items is not None:
            bulletin[section] = items
            changed = True

    if not changed:
        print("no sections refreshed")
        return

    new_json = json.dumps(bulletin, indent=1, ensure_ascii=False)
    html = html[: m.start()] + m.group(1) + new_json + m.group(3) + html[m.end():]

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print("content refreshed")


if __name__ == "__main__":
    main()
