"""Tool schemas and handlers for hindsight-curator."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _as_list(value: Any) -> Optional[list[str]]:
    if value is None or value == "":
        return None
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed]
            except json.JSONDecodeError:
                pass
        return [p.strip() for p in text.split(",") if p.strip()]
    return [str(value)]


def _as_skipped(value: Any) -> Optional[list[dict[str, str]]]:
    """Normalize optional skipped=[{id, why}, ...] from tool args (list or JSON string)."""
    if value is None or value == "":
        return None
    raw = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, list):
        return None
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            mid = str(item.get("id") or item.get("memory_id") or "").strip()
            why = str(item.get("why") or item.get("reason") or "").strip()
            if mid:
                out.append({"id": mid, "why": why or "coincidental match"})
        elif isinstance(item, str) and item.strip():
            out.append({"id": item.strip(), "why": "coincidental match"})
    return out or None


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "curate_scan": {
        "name": "curate_scan",
        "description": (
            "Keyword/regex SEARCH only — results are unverified matches, NOT confirmed stale "
            "facts. You MUST read each candidate excerpt (and full text when needed) and classify: "
            "stale (states the outdated fact) vs coincidental (same tokens for a file size, row "
            "count, price, etc.) vs keep (still true / owner preference). Do not invalidate from "
            "scan alone. Pass only verified-stale ids to curate_plan via candidate_ids; record "
            "coincidental matches in skipped."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword substring or /regex/flags over memory text.",
                },
                "since": {
                    "type": "string",
                    "description": "Optional ISO start_date bound (inclusive).",
                },
                "until": {
                    "type": "string",
                    "description": "Optional ISO end_date bound (exclusive).",
                },
                "types": {
                    "type": "string",
                    "description": "Optional fact types filter, comma-separated (world,experience,observation).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max candidates to return (default 50).",
                },
            },
            "required": ["query"],
        },
    },
    "curate_plan": {
        "name": "curate_plan",
        "description": (
            "Build a dry-run invalidation plan. Agent path: pass candidate_ids with ONLY memories "
            "you verified as stating the outdated fact, plus optional skipped=[{id, why}] for "
            "coincidental keyword hits left untouched. Query path (no candidate_ids) remains for "
            "/memory-curate and still scans by keyword. Does not PATCH — show the owner a short "
            "table (invalidate vs left untouched) and wait for explicit OK before curate_apply."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic query (used when candidate_ids omitted; /memory-curate path).",
                },
                "candidate_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Verified-stale memory ids only (agent path). Do not include coincidental matches."
                    ),
                },
                "skipped": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Memory id left untouched."},
                            "why": {
                                "type": "string",
                                "description": "Short reason (e.g. file size, row count, price).",
                            },
                        },
                        "required": ["id", "why"],
                    },
                    "description": (
                        "Coincidental matches left untouched: [{id, why}]. Stored on the plan and "
                        "shown as 'left untouched: why'."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Curation reason recorded on each invalidation.",
                },
                "keep": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Memory ids to protect (whitelist) and skip on apply.",
                },
            },
            "required": ["reason"],
        },
    },
    "curate_apply": {
        "name": "curate_apply",
        "description": (
            "Apply a previously created plan: PATCH each target to state=invalidated. "
            "Call ONLY after the owner explicitly confirmed this plan in the conversation "
            "(e.g. 'yes', 'apply it', 'OK'). Never apply on the same turn you first show the plan. "
            "Optional ids limits the apply to a subset of non-kept plan targets "
            "(Desktop checkbox selection). Continues on per-item failures; "
            "journals every result for restore."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "string",
                    "description": "Plan id returned by curate_plan.",
                },
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional subset of memory ids to invalidate. Omit to apply "
                        "all non-kept targets. Kept/whitelist ids are always skipped."
                    ),
                },
            },
            "required": ["plan_id"],
        },
    },
    "curate_restore": {
        "name": "curate_restore",
        "description": (
            "Restore memories after an apply: either by plan_id (journal previous states) "
            "or by explicit memory_ids."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan to roll back."},
                "memory_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit memory ids to restore to their journaled previous state.",
                },
            },
        },
    },
    "curate_checkpoint": {
        "name": "curate_checkpoint",
        "description": (
            "After a successful apply, save the new current truth in the owner's words "
            "(tagged state:current). Prefer the owner's phrasing of the updated fact."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Verified current-state fact to retain (prefer owner's wording).",
                },
                "context": {
                    "type": "string",
                    "description": "Optional retain context.",
                },
            },
            "required": ["text"],
        },
    },
    "curate_history": {
        "name": "curate_history",
        "description": "Show recent local curation journal entries (plans, applies, restores, checkpoints).",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max history rows (default 20).",
                },
            },
        },
    },
}


def make_handlers(get_curator: Callable[[], Any]) -> dict[str, Callable[[dict], str]]:
    """Build tool handlers closed over a curator factory."""

    def curate_scan(args: dict) -> str:
        c = get_curator()
        limit = args.get("limit", 50)
        try:
            limit_i = int(limit) if limit is not None else 50
        except (TypeError, ValueError):
            limit_i = 50
        return _dumps(
            c.scan(
                str(args.get("query") or ""),
                since=args.get("since") or None,
                until=args.get("until") or None,
                types=args.get("types"),
                limit=limit_i,
            )
        )

    def curate_plan(args: dict) -> str:
        c = get_curator()
        return _dumps(
            c.plan(
                query=args.get("query"),
                candidate_ids=_as_list(args.get("candidate_ids")),
                reason=str(args.get("reason") or ""),
                keep=_as_list(args.get("keep")),
                skipped=_as_skipped(args.get("skipped")),
            )
        )

    def curate_apply(args: dict) -> str:
        c = get_curator()
        ids = _as_list(args.get("ids")) if "ids" in args else None
        return _dumps(c.apply(str(args.get("plan_id") or ""), ids=ids))

    def curate_restore(args: dict) -> str:
        c = get_curator()
        return _dumps(
            c.restore(
                plan_id=(str(args["plan_id"]) if args.get("plan_id") else None),
                memory_ids=_as_list(args.get("memory_ids")),
            )
        )

    def curate_checkpoint(args: dict) -> str:
        c = get_curator()
        return _dumps(
            c.checkpoint(
                str(args.get("text") or ""),
                context=(str(args["context"]) if args.get("context") else None),
            )
        )

    def curate_history(args: dict) -> str:
        c = get_curator()
        limit = args.get("limit", 20)
        try:
            limit_i = int(limit) if limit is not None else 20
        except (TypeError, ValueError):
            limit_i = 20
        return _dumps(c.history(limit=limit_i))

    return {
        "curate_scan": curate_scan,
        "curate_plan": curate_plan,
        "curate_apply": curate_apply,
        "curate_restore": curate_restore,
        "curate_checkpoint": curate_checkpoint,
        "curate_history": curate_history,
    }
