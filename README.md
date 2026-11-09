# hindsight-curator

Find stale or contradicted Hindsight memories by topic, let the agent verify each keyword hit, show you a dry-run plan (including coincidental matches left untouched), invalidate only after you say yes, write one checkpoint fact, and roll everything back — from chat or the Desktop **Memory curation** page.

Hindsight’s own UI retires one fact at a time. This is a **separate** Hermes plugin on top of the public HTTP API; it does **not** replace or patch the official `hindsight` memory-provider plugin.

## How it works

1. **Keyword search** finds memories that mention the old term (e.g. `192K`). That list is not a verdict.
2. **The agent reads each match** and decides: retire only memories that actually state the outdated fact; leave coincidental hits (a file size, a row count, a price) untouched and record why.
3. **You confirm** the short invalidate / left-untouched table in chat (or tick boxes on Desktop). Nothing is PATCHed until then.
4. **Undo** is always available via restore from the local journal.

## 30-second quickstart

1. Install into Hermes (local path or catalog once published) and enable the plugin.
2. Settings → Plugins → **hindsight-curator**: set **API URL** (default `http://localhost:8888`) and **Bank ID** (default `hermes`).
3. If your server needs auth, set `HINDSIGHT_API_KEY` via Hermes secrets (optional for unauthenticated local servers).
4. In chat, say something like: *“Our model's context window changed from 192K to 1M — update that fact everywhere.”* The agent should scan, classify, show a plan, and **ask you** before applying. After you OK, it invalidates, checkpoints the new truth, and offers restore.

Slash helpers (keyword path, still dry-run until apply):

```text
/memory-curate help
/memory-curate scan 192K
/memory-curate plan "192K" --reason "2026-11 curation: window upgraded"
/memory-curate apply plan_…………
```

## Demo script

See [docs/DEMO.md](docs/DEMO.md). Seed a disposable bank (never a personal one):

```bash
python scripts/fake_hindsight.py --port 8899 --bank demo-curator --state /tmp/hc-demo.json
python scripts/seed_demo_bank.py --url http://127.0.0.1:8899 --bank demo-curator --fixed-ids
```

Live LLM eval (profile **hcdemo** only, fake server only):

```bash
python scripts/live_eval.py --run-id 1
```

### Live-eval result (run 1 — PASS)

Turn 1 planned and asked for confirmation (0 invalidations). Turn 2 applied after “Yes, apply it.”

| id | group | expected | actual | ok |
|----|-------|----------|--------|----|
| demo_stale_window | stale | invalidated | invalidated | Y |
| demo_stale_prompts | stale | invalidated | invalidated | Y |
| demo_stale_rag | stale | invalidated | invalidated | Y |
| demo_stale_budget | stale | invalidated | invalidated | Y |
| demo_stale_experiment | stale | invalidated | invalidated | Y |
| demo_stale_docs | stale | invalidated | invalidated | Y |
| demo_decoy_rows | decoy | valid | valid | Y |
| demo_decoy_logo | decoy | valid | valid | Y |
| demo_decoy_usd | decoy | valid | valid | Y |
| demo_keep_pref | keep | valid | valid | Y |
| demo_other_tuesday | other | valid | valid | Y |
| demo_other_journal | other | valid | valid | Y |
| demo_other_sqlite | other | valid | valid | Y |

Checkpoint: `The model's context window is 1M tokens.`

## Tools & commands

| Surface | What it does |
|---------|----------------|
| `curate_scan(query, …)` | Keyword/regex matches — **unverified**; agent must classify |
| `curate_plan(candidate_ids \| query, reason, keep?, skipped?)` | Dry-run plan; `skipped=[{id, why}]` = left untouched |
| `curate_apply(plan_id, ids?)` | Invalidate after **owner OK**; optional id subset |
| `curate_restore(plan_id \| memory_ids)` | Restore journaled previous states |
| `curate_checkpoint(text, context?)` | Retain current truth (`state:current`), prefer owner's words |
| `curate_history(limit?)` | Local journal of plans / applies / restores / checkpoints |
| `/memory-curate` | `help` · `scan` · `plan` · `apply` · `restore` · `history` |
| Skill `hindsight-curator:memory-curate` | Teaches the verify-before-retire workflow |
| Desktop **Memory curation** | Plan list + checkboxes + left-untouched reasons → Apply / Restore |

> Note: applying a subset marks the plan as applied; to invalidate the remaining items, create a new plan.

## Desktop

Unified package: enable both the **agent** half (tools + dashboard API) and the **Desktop** half in **Capabilities → Plugins**, then **Rescan** if needed. Open **Memory curation** from the sidebar (route `/memory-curation`).

![Memory curation page — screenshot placeholder](docs/screenshots/memory-curation.png)

The page lists stored plans, shows candidates with checkboxes (whitelist / left-untouched rows locked), and supports **Apply selected**, **Restore**, and **Refresh**.

## Data & network disclosure

- **Network:** HTTP(S) only to the configured Hindsight base URL (list, GET, PATCH, retain). No other hosts.
- **Writes to Hindsight:** `state=invalidated` (and restore `state=valid`) on selected memory ids; one retain for a checkpoint fact.
- **Local writes:** SQLite journal under the Hermes plugin data directory (`plans`, `apply_events`, whitelist, history, skipped reasons). Survives plugin updates; profile-scoped.
- **Secrets:** optional `HINDSIGHT_API_KEY` via Hermes `get_secret` / declared `optional_env`. Never reads the official hindsight plugin’s config files or `.env` directly.
- **Shell / background:** none (demo/eval scripts are manual operator tools, not started by the plugin).

## Limitations

- Observations are derived; plans auto-skip them (Hindsight rejects observation PATCH).
- Candidate matching is keyword/regex (+ optional date/type filters); the LLM must verify meaning.
- Desktop page needs the agent half enabled so the dashboard API mounts.
- Requires a reachable Hindsight HTTP API; does not embed or start a server (except optional demo fake).

## Development

```bash
# From the repo root, with Hermes 0.23 venv python:
python -m unittest discover -s tests -v

HERMES_HOME=/path/to/isolated/home hermes plugins validate . --install-deps
```

Tests use an in-thread fake Hindsight (`http.server`); they never call a real bank.

## License

License: TBD by author
