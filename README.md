# hindsight-curator

Find stale or contradicted Hindsight memories by topic, review a dry-run plan, invalidate them in bulk with a reason, write one checkpoint fact, and roll everything back — from chat.

Hindsight’s own UI retires one fact at a time. This is a **separate** Hermes plugin on top of the public HTTP API; it does **not** replace or patch the official `hindsight` memory-provider plugin.

## 30-second quickstart

1. Install into Hermes (local path or catalog once published).
2. Settings → Plugins → **hindsight-curator**: set **API URL** (default `http://localhost:8888`) and **Bank ID** (default `hermes`).
3. If your server needs auth, set `HINDSIGHT_API_KEY` via Hermes secrets / install prompt (optional for unauthenticated local servers).
4. In chat:

```text
/memory-curate scan 192K
/memory-curate plan "192K" --reason "2026-11 curation: window upgraded"
/memory-curate apply plan_…………
```

Then retain the current truth with the `curate_checkpoint` tool (or ask the agent to call it), and `/memory-curate restore <plan_id>` if you need a rollback.

## Demo script

See [docs/DEMO.md](docs/DEMO.md). Seed a disposable bank (never a personal one):

```bash
python scripts/seed_demo_bank.py --url http://127.0.0.1:8888 --bank demo-curator
```

## Tools & commands

| Surface | What it does |
|---------|----------------|
| `curate_scan(query, since?, until?, types?, limit?)` | List candidate memories (id, excerpt, date, type) |
| `curate_plan(query \| candidate_ids, reason, keep?)` | Store a **dry-run** plan (counts + sample); no PATCH |
| `curate_apply(plan_id)` | Invalidate plan targets; per-item ok / not_found / failed |
| `curate_restore(plan_id \| memory_ids)` | Restore journaled previous states |
| `curate_checkpoint(text, context?)` | Retain one current-truth fact (`state:current`) |
| `curate_history(limit?)` | Local journal of plans / applies / restores / checkpoints |
| `/memory-curate` | `scan` · `plan` · `apply` · `restore` · `history` |

## Data & network disclosure

- **Network:** HTTP(S) only to the configured Hindsight base URL (list, GET, PATCH, retain). No other hosts.
- **Writes to Hindsight:** `state=invalidated` (and restore `state=valid`) on selected memory ids; one retain for a checkpoint fact.
- **Local writes:** SQLite journal under the Hermes plugin data directory (`plans`, `apply_events`, whitelist, history). Survives plugin updates; profile-scoped.
- **Secrets:** optional `HINDSIGHT_API_KEY` via Hermes `get_secret` / declared `optional_env`. Never reads the official hindsight plugin’s config files or `.env` directly.
- **Shell / background:** none.
- **LLM auxiliary classification:** not used (OPEN — keyword/regex selection only).

## Limitations

- Observations are derived; plans auto-skip them (Hindsight rejects observation PATCH).
- Candidate matching is keyword/regex (+ optional date/type filters), not semantic recall.
- Desktop plan-review page is not shipped in this version (OPEN).
- Requires a reachable Hindsight HTTP API; does not embed or start a server.

## Development

```bash
# From the repo root, with Hermes 0.23 venv python:
python -m unittest discover -s tests -v

HERMES_HOME=/path/to/isolated/home hermes plugins validate . --install-deps
```

Tests use an in-thread fake Hindsight (`http.server`); they never call a real bank.

## License

License: TBD by author
