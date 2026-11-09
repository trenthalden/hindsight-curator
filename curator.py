"""Core curation workflow: scan → plan (dry-run) → apply → restore → checkpoint."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from .client import HindsightClient, HindsightError
from .journal import Journal


EXCERPT_LEN = 160


def _excerpt(text: str, n: int = EXCERPT_LEN) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _norm_types(types: Optional[Union[str, Sequence[str]]]) -> Optional[list[str]]:
    if types is None or types == "":
        return None
    if isinstance(types, str):
        parts = [p.strip() for p in types.split(",") if p.strip()]
        return parts or None
    return [str(t).strip() for t in types if str(t).strip()] or None


def _memory_fields(raw: dict[str, Any]) -> dict[str, Any]:
    mid = str(raw.get("id") or raw.get("memory_id") or "")
    text = str(raw.get("text") or raw.get("content") or "")
    fact_type = str(raw.get("fact_type") or raw.get("type") or "")
    state = str(raw.get("state") or "valid")
    date = (
        raw.get("date")
        or raw.get("mentioned_at")
        or raw.get("created_at")
        or raw.get("occurred_start")
        or ""
    )
    return {
        "id": mid,
        "text": text,
        "excerpt": _excerpt(text),
        "fact_type": fact_type,
        "type": fact_type,
        "state": state,
        "date": date,
    }


def _matches_query(text: str, query: str) -> bool:
    """Keyword / regex match. Bare words are substring (case-insensitive); `/pat/flags` is regex."""
    q = (query or "").strip()
    if not q:
        return True
    if len(q) >= 2 and q.startswith("/") and q.rfind("/") > 0:
        last = q.rfind("/")
        pattern = q[1:last]
        flags_s = q[last + 1 :]
        flags = 0
        if "i" in flags_s:
            flags |= re.IGNORECASE
        if "m" in flags_s:
            flags |= re.MULTILINE
        if "s" in flags_s:
            flags |= re.DOTALL
        try:
            return re.search(pattern, text or "", flags) is not None
        except re.error:
            return False
    return q.lower() in (text or "").lower()


@dataclass
class Curator:
    client: HindsightClient
    journal: Journal

    @classmethod
    def create(
        cls,
        *,
        data_dir: Path,
        api_url: str,
        bank_id: str,
        api_key: str = "",
        timeout: float = 30.0,
    ) -> "Curator":
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        journal = Journal(data_dir / "curator.db")
        client = HindsightClient(base_url=api_url, bank_id=bank_id, api_key=api_key, timeout=timeout)
        return cls(client=client, journal=journal)

    def scan(
        self,
        query: str,
        *,
        since: Optional[str] = None,
        until: Optional[str] = None,
        types: Optional[Union[str, Sequence[str]]] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        type_list = _norm_types(types)
        try:
            # Prefer server-side q= for simple substrings; always re-filter for regex / multi-type.
            server_q = None if (query or "").strip().startswith("/") else (query or None)
            page_limit = max(int(limit), 50)
            collected: list[dict[str, Any]] = []
            offset = 0
            total_seen = 0
            while len(collected) < limit and offset < 5000:
                raw = self.client.list_memories(
                    q=server_q,
                    state="valid",
                    types=type_list if type_list and len(type_list) == 1 else None,
                    start_date=since,
                    end_date=until,
                    limit=page_limit,
                    offset=offset,
                )
                items = raw.get("items") if isinstance(raw, dict) else None
                if not isinstance(items, list):
                    raise HindsightError("Unexpected list response shape", kind="bad_response")
                if not items:
                    break
                for unit in items:
                    total_seen += 1
                    if not isinstance(unit, dict):
                        continue
                    mem = _memory_fields(unit)
                    if not mem["id"]:
                        continue
                    if type_list and mem["fact_type"] and mem["fact_type"] not in type_list:
                        continue
                    if not _matches_query(mem["text"], query):
                        continue
                    if self.journal.is_whitelisted(mem["id"], mem["text"]):
                        mem["whitelisted"] = True
                    collected.append(mem)
                    if len(collected) >= limit:
                        break
                if len(items) < page_limit:
                    break
                offset += len(items)
            return {
                "ok": True,
                "query": query,
                "count": len(collected),
                "scanned": total_seen,
                "candidates": collected,
                "unverified": True,
                "note": (
                    "Keyword matches only — not verified. Read each excerpt and classify "
                    "before curate_plan (candidate_ids = verified stale; skipped = coincidental)."
                ),
            }
        except HindsightError as exc:
            return exc.as_dict()

    def plan(
        self,
        *,
        query: Optional[str] = None,
        candidate_ids: Optional[Sequence[str]] = None,
        reason: str,
        keep: Optional[Sequence[str]] = None,
        skipped: Optional[Sequence[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        reason = (reason or "").strip()
        if not reason:
            return {"ok": False, "error": "reason is required", "kind": "validation"}

        keep_ids = {str(x).strip() for x in (keep or []) if str(x).strip()}
        skipped_list: list[dict[str, str]] = []
        for row in skipped or []:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id") or row.get("memory_id") or "").strip()
            if not mid:
                continue
            why = str(row.get("why") or row.get("reason") or "").strip() or "coincidental match"
            skipped_list.append({"id": mid, "why": why})
        skipped_ids = {s["id"] for s in skipped_list}
        # Coincidental matches must not be invalidated even if also listed in candidate_ids.
        keep_ids |= skipped_ids
        items: list[dict[str, Any]] = []

        try:
            if candidate_ids:
                for mid in candidate_ids:
                    mid = str(mid).strip()
                    if not mid:
                        continue
                    try:
                        raw = self.client.get_memory(mid)
                        mem = _memory_fields(raw if isinstance(raw, dict) else {"id": mid})
                    except HindsightError as exc:
                        if exc.kind == "not_found":
                            continue
                        return exc.as_dict()
                    items.append(mem)
            elif query is not None and str(query).strip() != "":
                scanned = self.scan(str(query), limit=200)
                if not scanned.get("ok"):
                    return scanned
                items = list(scanned.get("candidates") or [])
            else:
                return {
                    "ok": False,
                    "error": "Provide query or candidate_ids",
                    "kind": "validation",
                }

            # Resolve skipped ids that are not already among plan items (for display / payload).
            known = {m["id"] for m in items}
            for sid in skipped_ids - known:
                try:
                    raw = self.client.get_memory(sid)
                    mem = _memory_fields(raw if isinstance(raw, dict) else {"id": sid})
                    items.append(mem)
                except HindsightError as exc:
                    if exc.kind == "not_found":
                        items.append(
                            {
                                "id": sid,
                                "text": "",
                                "excerpt": "",
                                "fact_type": "",
                                "type": "",
                                "state": "valid",
                                "date": "",
                            }
                        )
                    else:
                        return exc.as_dict()
        except HindsightError as exc:
            return exc.as_dict()

        # Whitelist + explicit keep are respected: they stay in the plan as kept=1, not invalidated.
        # Observations are derived — Hindsight rejects PATCH curation (400); skip them as kept.
        for mem in items:
            if mem.get("whitelisted") or self.journal.is_whitelisted(mem["id"], mem["text"]):
                keep_ids.add(mem["id"])
            if (mem.get("fact_type") or "").lower() == "observation":
                keep_ids.add(mem["id"])
        if keep:
            self.journal.add_whitelist(ids=keep, note=f"keep via plan: {reason[:80]}")

        plan_id = self.journal.create_plan(
            reason=reason,
            query=query if query is not None else None,
            items=items,
            kept_ids=keep_ids,
            skipped=skipped_list,
        )
        targets = [m for m in items if m["id"] not in keep_ids]
        return {
            "ok": True,
            "dry_run": True,
            "plan_id": plan_id,
            "reason": reason,
            "total_candidates": len(items),
            "to_invalidate": len(targets),
            "kept": len(keep_ids),
            "kept_ids": sorted(keep_ids),
            "skipped": skipped_list,
            "left_untouched": [
                {"id": s["id"], "why": s["why"]} for s in skipped_list
            ],
            "sample": [
                {"id": m["id"], "excerpt": m.get("excerpt") or _excerpt(m.get("text", "")), "type": m.get("fact_type")}
                for m in targets[:5]
            ],
            "note": (
                "Dry-run only. Show the owner invalidate vs left-untouched, wait for explicit "
                "confirmation, then call curate_apply(plan_id)."
            ),
        }

    def list_plans(self, limit: int = 50) -> dict[str, Any]:
        return {"ok": True, "plans": self.journal.list_plans(limit=limit)}

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        plan_id = (plan_id or "").strip()
        if not plan_id:
            return {"ok": False, "error": "plan_id is required", "kind": "validation"}
        plan = self.journal.get_plan(plan_id)
        if not plan:
            return {"ok": False, "error": f"Unknown plan_id: {plan_id}", "kind": "not_found"}
        payload = plan.get("payload") or {}
        skipped_list = list(payload.get("skipped") or [])
        why_by_id = {
            str(s.get("id")): str(s.get("why") or "")
            for s in skipped_list
            if isinstance(s, dict) and s.get("id")
        }
        items = []
        for it in plan.get("items") or []:
            kept = bool(it.get("kept"))
            mid = it["memory_id"]
            why = why_by_id.get(mid) or ""
            row = {
                "id": mid,
                "excerpt": it.get("text_excerpt") or "",
                "type": it.get("fact_type") or "",
                "previous_state": it.get("previous_state") or "valid",
                "kept": kept,
                "locked": kept,
            }
            if why:
                row["left_untouched"] = True
                row["why"] = why
            items.append(row)
        return {
            "ok": True,
            "plan_id": plan["plan_id"],
            "created_at": plan["created_at"],
            "reason": plan["reason"],
            "query": plan.get("query") or "",
            "status": plan["status"],
            "count": len(items),
            "items": items,
            "skipped": skipped_list,
            "left_untouched": [
                {"id": str(s.get("id")), "why": str(s.get("why") or "")}
                for s in skipped_list
                if isinstance(s, dict) and s.get("id")
            ],
            "apply_events": self.journal.apply_events_for_plan(plan_id),
        }

    def apply(self, plan_id: str, ids: Optional[Sequence[str]] = None) -> dict[str, Any]:
        plan_id = (plan_id or "").strip()
        if not plan_id:
            return {"ok": False, "error": "plan_id is required", "kind": "validation"}
        plan = self.journal.get_plan(plan_id)
        if not plan:
            return {"ok": False, "error": f"Unknown plan_id: {plan_id}", "kind": "not_found"}
        if plan["status"] == "applied":
            return {"ok": False, "error": f"Plan {plan_id} was already applied", "kind": "conflict"}

        targets = self.journal.plan_targets(plan_id)
        if ids is not None:
            wanted = {str(x).strip() for x in ids if str(x).strip()}
            targets = [t for t in targets if t["memory_id"] in wanted]
            if wanted and not targets:
                return {
                    "ok": False,
                    "error": "No matching apply targets for the given ids "
                    "(unknown, kept/whitelist, or not in plan)",
                    "kind": "validation",
                }
        reason = plan["reason"]
        results: list[dict[str, Any]] = []
        ok_n = not_found_n = failed_n = 0

        for item in targets:
            mid = item["memory_id"]
            prev = item.get("previous_state") or "valid"
            try:
                self.client.patch_memory(mid, {"state": "invalidated", "reason": reason})
                self.journal.record_apply(
                    plan_id=plan_id,
                    memory_id=mid,
                    previous_state=prev,
                    new_state="invalidated",
                    reason=reason,
                    result="ok",
                )
                results.append({"id": mid, "result": "ok"})
                ok_n += 1
            except HindsightError as exc:
                if exc.kind == "not_found" or exc.status == 404:
                    self.journal.record_apply(
                        plan_id=plan_id,
                        memory_id=mid,
                        previous_state=prev,
                        new_state=None,
                        reason=reason,
                        result="not_found",
                        detail=exc.message,
                    )
                    results.append({"id": mid, "result": "not_found", "detail": exc.message})
                    not_found_n += 1
                else:
                    self.journal.record_apply(
                        plan_id=plan_id,
                        memory_id=mid,
                        previous_state=prev,
                        new_state=None,
                        reason=reason,
                        result="failed",
                        detail=exc.message,
                    )
                    results.append({"id": mid, "result": "failed", "detail": exc.message})
                    failed_n += 1

        self.journal.mark_plan_applied(plan_id)
        summary = {
            "ok": True,
            "plan_id": plan_id,
            "invalidated": ok_n,
            "not_found": not_found_n,
            "failed": failed_n,
            "subset": ids is not None,
            "results": results,
        }
        self.journal.log_action(
            "apply",
            f"Applied {plan_id}: ok={ok_n} not_found={not_found_n} failed={failed_n}"
            + (f" subset={len(targets)}" if ids is not None else ""),
            plan_id=plan_id,
            detail={
                "invalidated": ok_n,
                "not_found": not_found_n,
                "failed": failed_n,
                "subset": ids is not None,
            },
        )
        return summary

    def restore(
        self,
        *,
        plan_id: Optional[str] = None,
        memory_ids: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        targets: list[tuple[str, Optional[str], Optional[str]]] = []
        # (memory_id, previous_state, source_plan)

        if plan_id:
            plan = self.journal.get_plan(plan_id)
            if not plan:
                return {"ok": False, "error": f"Unknown plan_id: {plan_id}", "kind": "not_found"}
            events = self.journal.apply_events_for_plan(plan_id)
            if events:
                for ev in events:
                    if ev.get("result") != "ok":
                        continue
                    targets.append((ev["memory_id"], ev.get("previous_state") or "valid", plan_id))
            else:
                # Plan never applied — restore planned previous states anyway if caller insists.
                for item in self.journal.plan_targets(plan_id):
                    targets.append((item["memory_id"], item.get("previous_state") or "valid", plan_id))
        elif memory_ids:
            for mid in memory_ids:
                mid = str(mid).strip()
                if not mid:
                    continue
                last = self.journal.last_apply_for_memory(mid)
                prev = (last or {}).get("previous_state") or "valid"
                src = (last or {}).get("plan_id")
                targets.append((mid, prev, src))
        else:
            return {
                "ok": False,
                "error": "Provide plan_id or memory_ids",
                "kind": "validation",
            }

        results: list[dict[str, Any]] = []
        ok_n = not_found_n = failed_n = 0
        for mid, prev, src in targets:
            restore_state = prev or "valid"
            try:
                self.client.patch_memory(mid, {"state": restore_state})
                results.append({"id": mid, "result": "ok", "state": restore_state, "plan_id": src})
                ok_n += 1
            except HindsightError as exc:
                if exc.kind == "not_found" or exc.status == 404:
                    results.append({"id": mid, "result": "not_found", "detail": exc.message})
                    not_found_n += 1
                else:
                    results.append({"id": mid, "result": "failed", "detail": exc.message})
                    failed_n += 1

        if plan_id:
            self.journal.mark_plan_restored(plan_id)
        self.journal.log_action(
            "restore",
            f"Restore plan={plan_id or '-'} ok={ok_n} not_found={not_found_n} failed={failed_n}",
            plan_id=plan_id,
            detail={"ok": ok_n, "not_found": not_found_n, "failed": failed_n},
        )
        return {
            "ok": True,
            "restored": ok_n,
            "not_found": not_found_n,
            "failed": failed_n,
            "results": results,
        }

    def checkpoint(self, text: str, *, context: Optional[str] = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "text is required", "kind": "validation"}
        tags = ["state:current", "source:hindsight-curator"]
        try:
            resp = self.client.retain(text, context=context, tags=tags)
        except HindsightError as exc:
            return exc.as_dict()
        self.journal.log_action(
            "checkpoint",
            f"Checkpoint retained ({len(text)} chars)",
            detail={"excerpt": _excerpt(text), "context": context},
        )
        return {
            "ok": True,
            "retained": True,
            "excerpt": _excerpt(text),
            "tags": tags,
            "response": resp if isinstance(resp, dict) else {"raw": resp},
        }

    def history(self, limit: int = 20) -> dict[str, Any]:
        return {"ok": True, "items": self.journal.history(limit=limit)}
