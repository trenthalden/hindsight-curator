# Changelog

## 0.2.0 — 2026-11-07

- Desktop **Memory curation** page (`desktop/plugin.js`): list plans, review candidates with checkboxes (whitelist locked), Apply selected / Restore / Refresh, per-item results.
- Dashboard backend (`dashboard/plugin_api.py`) for Desktop `ctx.rest` (`/plans`, apply, restore).
- `curate_apply(plan_id, ids?)` — optional subset of memory ids; omit `ids` for full plan apply (backward compatible).

## 0.1.0 — 2026-11-06

- Initial release: `curate_scan` / `curate_plan` / `curate_apply` / `curate_restore` / `curate_checkpoint` / `curate_history`.
- Slash command `/memory-curate` with scan, plan, apply, restore, history.
- Stdlib urllib client; SQLite apply journal with restore; whitelist / keep support.
- Dry-run plans by default; apply requires an explicit plan id.
- Unit + e2e tests against an in-thread fake Hindsight HTTP server.
- Demo seed script and shot list (`scripts/seed_demo_bank.py`, `docs/DEMO.md`).
