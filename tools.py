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


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "curate_scan": {
        "name": "curate_scan",
        "description": (
            "Scan the configured Hindsight bank for memories matching a topic query "
            "(keyword or /regex/i). Returns candidate id, text excerpt, date, and type. "
            "Does not modify any memories."
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
            "Build a dry-run invalidation plan from a query or explicit candidate ids. "
            "Respects the whitelist and optional keep ids. Does not PATCH anything — "
            "call curate_apply(plan_id) to execute."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic query (used when candidate_ids omitted).",
                },
                "candidate_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit memory ids to include in the plan.",
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
            "Retain one consolidated current-truth fact into the Hindsight bank "
            "(tagged state:current). Use after invalidating stale memories."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Verified current-state fact to retain.",
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
