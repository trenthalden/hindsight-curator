"""Slash command `/memory-curate`."""

from __future__ import annotations

import json
import shlex
from typing import Any, Callable, Optional


HELP = """\
/memory-curate — bulk-curate stale Hindsight memories

  scan <query> [--since ISO] [--until ISO] [--types world,experience] [--limit N]
  plan <query> --reason <text> [--keep id1,id2]
  apply <plan_id>
  restore <plan_id>
  history [limit]

Dry-run is the default: `plan` never PATCHes. `apply` requires an explicit plan id.
"""


def _parse_flags(tokens: list[str]) -> tuple[list[str], dict[str, str]]:
    positional: list[str] = []
    flags: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--") and len(tok) > 2:
            key = tok[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                flags[k] = v
            elif i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                flags[key] = tokens[i + 1]
                i += 1
            else:
                flags[key] = "true"
        else:
            positional.append(tok)
        i += 1
    return positional, flags


def make_handler(get_curator: Callable[[], Any], tool_handlers: dict[str, Callable[[dict], str]]) -> Callable[[str], str]:
    def handle(raw_args: str) -> str:
        text = (raw_args or "").strip()
        if not text or text in {"help", "-h", "--help"}:
            return HELP
        try:
            tokens = shlex.split(text)
        except ValueError as exc:
            return f"Could not parse arguments: {exc}\n\n{HELP}"
        if not tokens:
            return HELP

        sub = tokens[0].lower()
        rest, flags = _parse_flags(tokens[1:])

        try:
            if sub == "scan":
                query = " ".join(rest).strip()
                if not query:
                    return "Usage: /memory-curate scan <query> [--since ISO] [--until ISO] [--types …] [--limit N]"
                args: dict[str, Any] = {"query": query}
                if "since" in flags:
                    args["since"] = flags["since"]
                if "until" in flags:
                    args["until"] = flags["until"]
                if "types" in flags:
                    args["types"] = flags["types"]
                if "limit" in flags:
                    args["limit"] = int(flags["limit"])
                return tool_handlers["curate_scan"](args)

            if sub == "plan":
                query = " ".join(rest).strip()
                reason = flags.get("reason") or ""
                if not query or not reason:
                    return "Usage: /memory-curate plan <query> --reason <text> [--keep id1,id2]"
                args = {"query": query, "reason": reason}
                if "keep" in flags:
                    args["keep"] = [p.strip() for p in flags["keep"].split(",") if p.strip()]
                return tool_handlers["curate_plan"](args)

            if sub == "apply":
                if not rest:
                    return "Usage: /memory-curate apply <plan_id>"
                return tool_handlers["curate_apply"]({"plan_id": rest[0]})

            if sub == "restore":
                if not rest:
                    return "Usage: /memory-curate restore <plan_id>"
                return tool_handlers["curate_restore"]({"plan_id": rest[0]})

            if sub == "history":
                limit = 20
                if rest:
                    try:
                        limit = int(rest[0])
                    except ValueError:
                        return "Usage: /memory-curate history [limit]"
                if "limit" in flags:
                    limit = int(flags["limit"])
                return tool_handlers["curate_history"]({"limit": limit})

            return f"Unknown subcommand: {sub}\n\n{HELP}"
        except Exception as exc:  # noqa: BLE001 — surface cleanly in chat
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    return handle
