#!/usr/bin/env python3
"""Seed a Hindsight bank with synthetic project memories for the demo / live eval.

Includes:
  - ≥5 memories that state the old 192K context window (stale)
  - ≥3 coincidental decoys that mention 192K/192k (file size, rows, budget)
  - ≥3 unrelated facts
  - 1 owner preference that mentions the window but must be kept

Usage:
  python scripts/seed_demo_bank.py --url http://127.0.0.1:PORT --bank demo-curator
  python scripts/seed_demo_bank.py --url … --bank … --fixed-ids   # POST not used; for fake seed JSON

Does NOT target any server unless you pass --url. Never point this at a personal bank.
Ground truth labels live in scripts/demo_truth.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


# Stable ids for the fake server / live eval (demo_truth.json keys these).
DEMO_MEMORIES: list[dict[str, Any]] = [
    # —— stale: state the old context window ——
    {
        "id": "demo_stale_window",
        "text": "Our model's context window is 192K tokens.",
        "fact_type": "world",
        "tag": "stale",
    },
    {
        "id": "demo_stale_prompts",
        "text": "Keep prompts under 192K so the agent does not truncate mid-turn.",
        "fact_type": "world",
        "tag": "stale",
    },
    {
        "id": "demo_stale_rag",
        "text": "192k window limits RAG chunks to roughly 8K tokens each in the indexer.",
        "fact_type": "world",
        "tag": "stale",
    },
    {
        "id": "demo_stale_budget",
        "text": "Token budget warnings fire at 180K of the 192K context window.",
        "fact_type": "world",
        "tag": "stale",
    },
    {
        "id": "demo_stale_experiment",
        "text": "The long-context experiment still needs a 192K window for the baseline run.",
        "fact_type": "experience",
        "tag": "stale",
    },
    {
        "id": "demo_stale_docs",
        "text": "Project docs say the chat model ships with a 192K context window.",
        "fact_type": "world",
        "tag": "stale",
    },
    # —— decoys: coincidental 192K / 192k ——
    {
        "id": "demo_decoy_rows",
        "text": "The exported dataset has 192K rows after deduplication.",
        "fact_type": "world",
        "tag": "decoy",
    },
    {
        "id": "demo_decoy_logo",
        "text": "logo.png is 192K on disk; keep it under the asset budget.",
        "fact_type": "world",
        "tag": "decoy",
    },
    {
        "id": "demo_decoy_usd",
        "text": "Cloud spend budget: 192K USD per year for inference.",
        "fact_type": "world",
        "tag": "decoy",
    },
    # —— unrelated ——
    {
        "id": "demo_other_tuesday",
        "text": "Deployments are reviewed on Tuesday evenings.",
        "fact_type": "world",
        "tag": "other",
    },
    {
        "id": "demo_other_journal",
        "text": "Curation plans are dry-run until apply is called with a plan id.",
        "fact_type": "world",
        "tag": "other",
    },
    {
        "id": "demo_other_sqlite",
        "text": "SQLite journaling lives in the plugin data directory.",
        "fact_type": "world",
        "tag": "other",
    },
    # —— keep: owner preference that mentions the window ——
    {
        "id": "demo_keep_pref",
        "text": (
            "Owner preference: when summarizing long threads, remind me if we are "
            "near the old 192K habit even after the upgrade — I still paste huge logs."
        ),
        "fact_type": "experience",
        "tag": "keep",
    },
]


def retain(url: str, bank: str, api_key: str, text: str, tags: list[str]) -> dict[str, Any]:
    path = f"/v1/default/banks/{urllib.parse.quote(bank, safe='')}/memories"
    body = json.dumps({"items": [{"content": text, "tags": tags}]}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "hindsight-curator-seed/0.2.1",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url.rstrip("/") + path, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8") or "{}")


def seed_fixed(url: str, bank: str, api_key: str) -> int:
    """Seed via the fake server's /__seed admin endpoint (fixed ids)."""
    path = "/__seed"
    body = json.dumps(
        {
            "bank_id": bank,
            "memories": [
                {
                    "id": m["id"],
                    "text": m["text"],
                    "fact_type": m["fact_type"],
                    "state": "valid",
                    "created_at": "2026-09-01T00:00:00Z",
                    "mentioned_at": "2026-09-01T00:00:00Z",
                    "tags": ["demo:hindsight-curator", f"demo:{m['tag']}"],
                }
                for m in DEMO_MEMORIES
            ],
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "hindsight-curator-seed/0.2.1",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url.rstrip("/") + path, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8") or "{}")
    return int(data.get("count") or 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Hindsight base URL (e.g. http://127.0.0.1:8899)")
    parser.add_argument("--bank", required=True, help="Bank id to seed")
    parser.add_argument("--api-key", default="", help="Optional Bearer token")
    parser.add_argument(
        "--fixed-ids",
        action="store_true",
        help="Use fake-server /__seed so demo ids match scripts/demo_truth.json",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print facts only; do not call the server")
    args = parser.parse_args(argv)

    print(f"Seeding {len(DEMO_MEMORIES)} facts into bank={args.bank!r} at {args.url!r}")
    if args.dry_run:
        for i, fact in enumerate(DEMO_MEMORIES, 1):
            print(f"{i:02d}. [{fact['tag']}] {fact['id']}: {fact['text']}")
        return 0

    if args.fixed_ids:
        try:
            n = seed_fixed(args.url, args.bank, args.api_key)
            print(f"Done: {n} memories seeded with fixed ids")
            return 0 if n == len(DEMO_MEMORIES) else 1
        except Exception as exc:  # noqa: BLE001
            print(f"FIXED-ID seed failed: {exc}", file=sys.stderr)
            return 1

    ok = 0
    for fact in DEMO_MEMORIES:
        tags = ["demo:hindsight-curator", f"demo:{fact['tag']}"]
        try:
            retain(args.url, args.bank, args.api_key, fact["text"], tags)
            ok += 1
            print(f"  retained: {fact['text'][:72]}")
        except urllib.error.HTTPError as exc:
            print(f"  FAILED ({exc.code}): {fact['text'][:72]}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}", file=sys.stderr)
    print(f"Done: {ok}/{len(DEMO_MEMORIES)} retained")
    return 0 if ok == len(DEMO_MEMORIES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
