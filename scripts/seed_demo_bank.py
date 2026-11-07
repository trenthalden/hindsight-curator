#!/usr/bin/env python3
"""Seed a Hindsight bank with ~40 synthetic facts (including stale ones) for the demo.

Usage:
  python scripts/seed_demo_bank.py --url http://127.0.0.1:8888 --bank demo-curator [--api-key KEY]

Does NOT target any server unless you pass --url. Intended for a local/demo Hindsight
instance you control — never point this at a production or personal bank.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


FACTS: list[dict[str, Any]] = [
    # Stale / superseded (demo topic: context window + model)
    {"text": "The agent context window is 192K tokens.", "fact_type": "world", "tag": "stale"},
    {"text": "Daily work uses model X as the default chat model.", "fact_type": "world", "tag": "stale"},
    {"text": "Model X autostart is enabled for every session.", "fact_type": "world", "tag": "stale"},
    {"text": "Switch routing uses script switch_model_x.sh.", "fact_type": "world", "tag": "stale"},
    {"text": "The long-context experiment still needs a 192K window.", "fact_type": "experience", "tag": "stale"},
    {"text": "Benchmark notes assume model X latency under 2s.", "fact_type": "world", "tag": "stale"},
    {"text": "Fallback for model X outages is a local 8B checkpoint.", "fact_type": "world", "tag": "stale"},
    {"text": "Token budget warnings fire at 180K of the 192K window.", "fact_type": "world", "tag": "stale"},
    # Durable preferences (whitelist candidates)
    {"text": "Prefer concise answers with sources when recalling config.", "fact_type": "experience", "tag": "keep"},
    {"text": "Never delete memory banks without an explicit restore plan.", "fact_type": "experience", "tag": "keep"},
    {"text": "Owner prefers model X only for offline drafts, not daily chat.", "fact_type": "experience", "tag": "keep"},
    # Unrelated current facts
    {"text": "Deployments are reviewed on Tuesday evenings.", "fact_type": "world", "tag": "other"},
    {"text": "The demo bank lives on the local Hindsight instance.", "fact_type": "world", "tag": "other"},
    {"text": "Plugin data is stored under the Hermes plugin-data directory.", "fact_type": "world", "tag": "other"},
    {"text": "Slash commands are registered through the Hermes plugin API.", "fact_type": "world", "tag": "other"},
    {"text": "Unit tests use an in-thread fake HTTP server.", "fact_type": "world", "tag": "other"},
    {"text": "Curation plans are dry-run until apply is called with a plan id.", "fact_type": "world", "tag": "other"},
    {"text": "Invalidation is reversible through the Hindsight PATCH API.", "fact_type": "world", "tag": "other"},
    {"text": "Observations are derived and should not be PATCHed directly.", "fact_type": "world", "tag": "other"},
    {"text": "Retain accepts one or more content items per request.", "fact_type": "world", "tag": "other"},
    {"text": "The journal records previous state for every successful apply.", "fact_type": "world", "tag": "other"},
    {"text": "Whitelist patterns protect owner decisions during plan generation.", "fact_type": "world", "tag": "other"},
    {"text": "Auth uses a Bearer token when the server requires it.", "fact_type": "world", "tag": "other"},
    {"text": "Local default API URL is http://localhost:8888.", "fact_type": "world", "tag": "other"},
    {"text": "Default bank id for Hermes integrations is hermes.", "fact_type": "world", "tag": "other"},
    {"text": "Demo facts are synthetic and contain no personal data.", "fact_type": "world", "tag": "other"},
    {"text": "Checkpoint facts are tagged state:current after curation.", "fact_type": "world", "tag": "other"},
    {"text": "Scan supports keyword and /regex/i query forms.", "fact_type": "world", "tag": "other"},
    {"text": "Date bounds use start_date and end_date query parameters.", "fact_type": "world", "tag": "other"},
    {"text": "Partial apply failures do not abort the rest of the plan.", "fact_type": "world", "tag": "other"},
    {"text": "A 404 on a rebuilt observation id is treated as not_found.", "fact_type": "world", "tag": "other"},
    {"text": "History lists recent plan, apply, restore, and checkpoint actions.", "fact_type": "world", "tag": "other"},
    {"text": "The seed script is for demo banks only.", "fact_type": "world", "tag": "other"},
    {"text": "Network calls go only to the configured Hindsight base URL.", "fact_type": "world", "tag": "other"},
    {"text": "No credentials are read from other plugins' config files.", "fact_type": "world", "tag": "other"},
    {"text": "Stdlib urllib is the only HTTP client used by this plugin.", "fact_type": "world", "tag": "other"},
    {"text": "SQLite journaling lives in the plugin data directory.", "fact_type": "world", "tag": "other"},
    {"text": "Restore re-PATCHes memories back to their journaled previous state.", "fact_type": "world", "tag": "other"},
    {"text": "The /memory-curate slash command mirrors the tool surface.", "fact_type": "world", "tag": "other"},
    {"text": "Catalog admission requires hermes plugins validate to pass.", "fact_type": "world", "tag": "other"},
]


def retain(url: str, bank: str, api_key: str, text: str, tags: list[str]) -> dict[str, Any]:
    path = f"/v1/default/banks/{urllib.parse.quote(bank, safe='')}/memories"
    body = json.dumps({"items": [{"content": text, "tags": tags}]}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "hindsight-curator-seed/0.1"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url.rstrip("/") + path, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8") or "{}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Hindsight base URL (e.g. http://127.0.0.1:8888)")
    parser.add_argument("--bank", required=True, help="Bank id to seed")
    parser.add_argument("--api-key", default="", help="Optional Bearer token")
    parser.add_argument("--dry-run", action="store_true", help="Print facts only; do not call the server")
    args = parser.parse_args(argv)

    print(f"Seeding {len(FACTS)} facts into bank={args.bank!r} at {args.url!r}")
    if args.dry_run:
        for i, fact in enumerate(FACTS, 1):
            print(f"{i:02d}. [{fact['tag']}] {fact['text']}")
        return 0

    ok = 0
    for fact in FACTS:
        tags = ["demo:hindsight-curator", f"demo:{fact['tag']}"]
        try:
            retain(args.url, args.bank, args.api_key, fact["text"], tags)
            ok += 1
            print(f"  retained: {fact['text'][:72]}")
        except urllib.error.HTTPError as exc:
            print(f"  FAILED ({exc.code}): {fact['text'][:72]}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}", file=sys.stderr)
    print(f"Done: {ok}/{len(FACTS)} retained")
    return 0 if ok == len(FACTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
