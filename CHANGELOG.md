# Changelog

## 0.1.0 — 2026-11-06

- Initial release: `curate_scan` / `curate_plan` / `curate_apply` / `curate_restore` / `curate_checkpoint` / `curate_history`.
- Slash command `/memory-curate` with scan, plan, apply, restore, history.
- Stdlib urllib client; SQLite apply journal with restore; whitelist / keep support.
- Dry-run plans by default; apply requires an explicit plan id.
- Unit + e2e tests against an in-thread fake Hindsight HTTP server.
- Demo seed script and shot list (`scripts/seed_demo_bank.py`, `docs/DEMO.md`).
