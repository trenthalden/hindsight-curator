# Demo shot list (< 90 s)

Goal: show stale Hindsight recall cleaned up in one chat flow, then rolled back.

## Prep (before recording)

1. Local Hindsight listening (example: `http://127.0.0.1:8888`), empty demo bank `demo-curator`.
2. Install/enable this plugin in a throwaway Hermes profile; set plugin settings:
   - API URL → your local URL
   - Bank ID → `demo-curator`
3. Seed (do **not** use a personal bank):

```bash
python scripts/seed_demo_bank.py --url http://127.0.0.1:8888 --bank demo-curator
```

4. Optional: confirm recall still surfaces the stale line (via Hindsight UI or the memory provider’s recall tool):
   - Query: `context window` → expect “192K”.

## Shot list

| t | Screen | Say / type | Expect |
|---|--------|------------|--------|
| 0–10s | Chat | “What’s our context window?” (or recall tool) | Stale **192K** fact appears |
| 10–25s | Chat | `/memory-curate scan 192K` | Candidates with ids, excerpts, types |
| 25–45s | Chat | `/memory-curate plan "/192K|model X/i" --reason "2026-11 demo: upgraded window+model" --keep <keep-id>` | Dry-run plan id, counts, sample; **no** invalidation yet |
| 45–60s | Chat | `/memory-curate apply <plan_id>` then ask agent to `curate_checkpoint` with current truth (“1M tokens; model Y”) | Per-item ok/not_found/failed; checkpoint retained |
| 60–75s | Chat | Re-ask / recall `context window` | Checkpoint / current truth leads; stale 192K gone |
| 75–90s | Chat | `/memory-curate restore <plan_id>` | Original states restored; optional history flash |

## Alternate (tools only, no slash)

Same arc via tools: `curate_scan` → `curate_plan` → `curate_apply` → `curate_checkpoint` → recall → `curate_restore`.

## Notes for judges

- Dry-run is default; apply needs an explicit plan id.
- Whitelist/`keep` protects owner preferences that mention the old topic.
- This plugin does **not** replace the official `hindsight` memory provider.
