"""SQLite journal for curation plans, apply results, whitelist, and history."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Journal:
    """Local audit trail under the plugin data directory."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS plans (
                plan_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                reason TEXT NOT NULL,
                query TEXT,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plan_items (
                plan_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                previous_state TEXT,
                text_excerpt TEXT,
                fact_type TEXT,
                kept INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (plan_id, memory_id),
                FOREIGN KEY (plan_id) REFERENCES plans(plan_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS apply_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                previous_state TEXT,
                new_state TEXT,
                reason TEXT,
                result TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (plan_id) REFERENCES plans(plan_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS whitelist (
                kind TEXT NOT NULL,
                value TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (kind, value)
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                action TEXT NOT NULL,
                plan_id TEXT,
                summary TEXT NOT NULL,
                detail_json TEXT
            );
            """
        )
        self._conn.commit()

    # ── whitelist ──────────────────────────────────────────────────────────

    def add_whitelist(self, *, ids: Iterable[str] = (), patterns: Iterable[str] = (), note: str = "") -> int:
        now = _utc_now()
        n = 0
        for memory_id in ids:
            mid = str(memory_id).strip()
            if not mid:
                continue
            self._conn.execute(
                "INSERT OR REPLACE INTO whitelist(kind, value, note, created_at) VALUES (?,?,?,?)",
                ("id", mid, note, now),
            )
            n += 1
        for pattern in patterns:
            pat = str(pattern).strip()
            if not pat:
                continue
            self._conn.execute(
                "INSERT OR REPLACE INTO whitelist(kind, value, note, created_at) VALUES (?,?,?,?)",
                ("pattern", pat, note, now),
            )
            n += 1
        self._conn.commit()
        return n

    def list_whitelist(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT kind, value, note, created_at FROM whitelist ORDER BY kind, value"
        ).fetchall()
        return [dict(r) for r in rows]

    def is_whitelisted(self, memory_id: str, text: str = "") -> bool:
        mid = str(memory_id)
        row = self._conn.execute(
            "SELECT 1 FROM whitelist WHERE kind='id' AND value=? LIMIT 1", (mid,)
        ).fetchone()
        if row:
            return True
        patterns = self._conn.execute(
            "SELECT value FROM whitelist WHERE kind='pattern'"
        ).fetchall()
        hay = text or ""
        for (pat,) in patterns:
            try:
                import re

                if re.search(pat, hay, re.IGNORECASE):
                    return True
            except re.error:
                if pat.lower() in hay.lower():
                    return True
        return False

    # ── plans ──────────────────────────────────────────────────────────────

    def create_plan(
        self,
        *,
        reason: str,
        query: Optional[str],
        items: list[dict[str, Any]],
        kept_ids: set[str],
        skipped: Optional[list[dict[str, str]]] = None,
    ) -> str:
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        skipped_norm: list[dict[str, str]] = []
        for row in skipped or []:
            mid = str(row.get("id") or "").strip()
            if not mid:
                continue
            skipped_norm.append(
                {
                    "id": mid,
                    "why": str(row.get("why") or "coincidental match").strip() or "coincidental match",
                }
            )
        payload = {
            "reason": reason,
            "query": query,
            "count": len(items),
            "kept": sorted(kept_ids),
            "skipped": skipped_norm,
            "sample": items[:5],
        }
        self._conn.execute(
            "INSERT INTO plans(plan_id, created_at, reason, query, status, payload_json) VALUES (?,?,?,?,?,?)",
            (plan_id, now, reason, query or "", "dry_run", json.dumps(payload)),
        )
        for item in items:
            mid = item["id"]
            self._conn.execute(
                "INSERT INTO plan_items(plan_id, memory_id, previous_state, text_excerpt, fact_type, kept) "
                "VALUES (?,?,?,?,?,?)",
                (
                    plan_id,
                    mid,
                    item.get("state") or "valid",
                    (item.get("text") or "")[:240],
                    item.get("fact_type") or item.get("type") or "",
                    1 if mid in kept_ids else 0,
                ),
            )
        self._log(
            now,
            "plan",
            plan_id,
            f"Dry-run plan {plan_id}: {len(items)} candidate(s), {len(kept_ids)} kept, "
            f"{len(skipped_norm)} left untouched",
            payload,
        )
        self._conn.commit()
        return plan_id

    def list_plans(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT plan_id, created_at, reason, query, status, payload_json FROM plans "
            "ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            skipped = payload.get("skipped") or []
            out.append(
                {
                    "plan_id": r["plan_id"],
                    "created_at": r["created_at"],
                    "reason": r["reason"],
                    "query": r["query"] or "",
                    "status": r["status"],
                    "count": int(payload.get("count") or 0),
                    "kept": len(payload.get("kept") or []),
                    "skipped": len(skipped) if isinstance(skipped, list) else 0,
                }
            )
        return out

    def get_plan(self, plan_id: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute("SELECT * FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
        if not row:
            return None
        items = self._conn.execute(
            "SELECT memory_id, previous_state, text_excerpt, fact_type, kept FROM plan_items "
            "WHERE plan_id=? ORDER BY kept ASC, memory_id",
            (plan_id,),
        ).fetchall()
        return {
            "plan_id": row["plan_id"],
            "created_at": row["created_at"],
            "reason": row["reason"],
            "query": row["query"],
            "status": row["status"],
            "payload": json.loads(row["payload_json"]),
            "items": [dict(i) for i in items],
        }

    def plan_targets(self, plan_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT memory_id, previous_state, text_excerpt, fact_type FROM plan_items "
            "WHERE plan_id=? AND kept=0 ORDER BY memory_id",
            (plan_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_plan_applied(self, plan_id: str) -> None:
        self._conn.execute("UPDATE plans SET status=? WHERE plan_id=?", ("applied", plan_id))
        self._conn.commit()

    def mark_plan_restored(self, plan_id: str) -> None:
        self._conn.execute("UPDATE plans SET status=? WHERE plan_id=?", ("restored", plan_id))
        self._conn.commit()

    def record_apply(
        self,
        *,
        plan_id: str,
        memory_id: str,
        previous_state: Optional[str],
        new_state: Optional[str],
        reason: str,
        result: str,
        detail: str = "",
    ) -> None:
        self._conn.execute(
            "INSERT INTO apply_events(plan_id, memory_id, previous_state, new_state, reason, result, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (plan_id, memory_id, previous_state, new_state, reason, result, detail, _utc_now()),
        )
        self._conn.commit()

    def apply_events_for_plan(self, plan_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT memory_id, previous_state, new_state, reason, result, detail, created_at "
            "FROM apply_events WHERE plan_id=? ORDER BY id",
            (plan_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def last_apply_for_memory(self, memory_id: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT plan_id, memory_id, previous_state, new_state, reason, result "
            "FROM apply_events WHERE memory_id=? AND result='ok' ORDER BY id DESC LIMIT 1",
            (memory_id,),
        ).fetchall()
        return dict(row[0]) if row else None

    def _log(self, created_at: str, action: str, plan_id: Optional[str], summary: str, detail: Any = None) -> None:
        self._conn.execute(
            "INSERT INTO history(created_at, action, plan_id, summary, detail_json) VALUES (?,?,?,?,?)",
            (created_at, action, plan_id, summary, json.dumps(detail) if detail is not None else None),
        )

    def log_action(self, action: str, summary: str, *, plan_id: Optional[str] = None, detail: Any = None) -> None:
        self._log(_utc_now(), action, plan_id, summary, detail)
        self._conn.commit()

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT created_at, action, plan_id, summary, detail_json FROM history "
            "ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        out = []
        for r in rows:
            item = {
                "created_at": r["created_at"],
                "action": r["action"],
                "plan_id": r["plan_id"],
                "summary": r["summary"],
            }
            if r["detail_json"]:
                try:
                    item["detail"] = json.loads(r["detail_json"])
                except json.JSONDecodeError:
                    item["detail"] = r["detail_json"]
            out.append(item)
        return out
