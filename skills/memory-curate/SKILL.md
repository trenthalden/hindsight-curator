---
name: memory-curate
description: >
  Update a changed fact across Hindsight memories: keyword scan, read and
  classify each match, plan only verified-stale ids (skip coincidental hits),
  show the owner a short table, wait for explicit OK, apply, checkpoint, offer
  restore. Use when the owner says a fact changed and should be updated
  everywhere in memory.
---

# Memory curation workflow

Use this skill whenever the owner says a project fact changed and must be
updated everywhere in memory (for example: context window, model name, URL).

## Hard rules

1. **Scan is keyword search only.** Matches are unverified.
2. **You must read every match** and classify it before planning.
3. **Never call `curate_apply` until the owner explicitly confirms** this plan
   in the conversation (e.g. "yes", "apply it", "OK"). Showing a plan is not consent.
4. Prefer the agent path: `candidate_ids` + `skipped`, not blind query planning.

## Steps

### 1. Scan

Call `curate_scan` with a keyword or `/regex/i` that catches the old fact
(e.g. `192K`, `/192\s*[kK]/`).

Treat results as candidates only.

### 2. Read and classify each candidate

For every hit, decide:

| Class | Meaning | Action |
|-------|---------|--------|
| **stale** | Memory states the outdated fact | Include id in `candidate_ids` |
| **decoy / coincidental** | Same tokens by accident (file size, row count, price, bytes) | Put `{id, why}` in `skipped` |
| **keep** | Still true, or an owner preference that mentions the old value on purpose | Put id in `keep` (and/or `skipped` with why) |
| **unrelated** | No real tie to the change | Leave out of the plan |

Examples of coincidental "192K": dataset row counts, image file size, budget USD.

### 3. Plan (dry-run)

```text
curate_plan(
  candidate_ids=[…verified stale ids…],
  skipped=[{id, why}, …],
  keep=[…preference ids…],
  reason="…short curation reason…"
)
```

Do **not** PATCH yet.

### 4. Show the owner a short table

Present two columns (or sections):

- **Invalidate** — id + short excerpt
- **Left untouched** — id + why

Ask clearly: whether to apply this plan. Stop and wait.

### 5. After explicit OK → apply

Only then: `curate_apply(plan_id=…)`.

### 6. Checkpoint the new truth

`curate_checkpoint(text=…)` using the **owner's wording** of the new fact
(e.g. "The model's context window is 1M tokens.").

### 7. Offer restore

Tell the owner they can undo with `curate_restore(plan_id=…)` or
`/memory-curate restore <plan_id>`.

## Slash command note

`/memory-curate plan <query> --reason …` still uses the keyword query path for
quick CLI use. In chat agent turns, prefer verified `candidate_ids` + `skipped`.
