"""In-thread fake Hindsight HTTP server for unit/e2e tests (stdlib only)."""

from __future__ import annotations

import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse


class FakeHindsightState:
    def __init__(self, *, api_key: Optional[str] = None, bank_id: str = "demo"):
        self.api_key = api_key
        self.bank_id = bank_id
        self.memories: dict[str, dict[str, Any]] = {}
        self.patch_calls: list[dict[str, Any]] = []
        self.retain_calls: list[dict[str, Any]] = []
        self.fail_ids: set[str] = set()  # return 500 on PATCH
        self.gone_ids: set[str] = set()  # return 404 on PATCH (e.g. rebuilt observations)
        self.down = False
        self._lock = threading.Lock()

    def seed(self, items: list[dict[str, Any]]) -> None:
        with self._lock:
            for item in items:
                mid = item.get("id") or f"mem_{uuid.uuid4().hex[:8]}"
                self.memories[mid] = {
                    "id": mid,
                    "text": item.get("text") or item.get("content") or "",
                    "fact_type": item.get("fact_type") or item.get("type") or "world",
                    "state": item.get("state") or "valid",
                    "created_at": item.get("created_at") or item.get("date") or "2026-01-01T00:00:00Z",
                    "mentioned_at": item.get("mentioned_at") or item.get("date") or "2026-01-01T00:00:00Z",
                    "context": item.get("context") or "",
                }

    def reset_counters(self) -> None:
        with self._lock:
            self.patch_calls.clear()
            self.retain_calls.clear()


def make_handler(state: FakeHindsightState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # silence
            return

        def _read_json(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {}

        def _send(self, code: int, body: Any) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _check_auth(self) -> bool:
            if state.api_key is None:
                return True
            auth = self.headers.get("Authorization") or ""
            expected = f"Bearer {state.api_key}"
            if auth != expected:
                self._send(401, {"detail": "unauthorized"})
                return False
            return True

        def _bank_paths(self) -> Optional[re.Match[str]]:
            path = urlparse(self.path).path
            return re.match(
                r"^/v1/default/banks/(?P<bank>[^/]+)/memories(?P<rest>/.*)?$",
                path,
            )

        def do_GET(self) -> None:  # noqa: N802
            if state.down:
                self.close_connection = True
                return
            if not self._check_auth():
                return
            m = self._bank_paths()
            if not m:
                self._send(404, {"detail": "not found"})
                return
            bank = m.group("bank")
            rest = m.group("rest") or ""
            if bank != state.bank_id:
                self._send(404, {"detail": f"bank {bank} not found"})
                return
            qs = parse_qs(urlparse(self.path).query)

            if rest in ("/list", ""):
                q = (qs.get("q") or [None])[0]
                mem_state = (qs.get("state") or [None])[0]
                mem_type = (qs.get("type") or [None])[0]
                start = (qs.get("start_date") or [None])[0]
                end = (qs.get("end_date") or [None])[0]
                try:
                    limit = int((qs.get("limit") or ["100"])[0])
                except ValueError:
                    limit = 100
                try:
                    offset = int((qs.get("offset") or ["0"])[0])
                except ValueError:
                    offset = 0

                with state._lock:
                    items = list(state.memories.values())
                filtered = []
                for unit in items:
                    if mem_state and unit.get("state") != mem_state:
                        continue
                    if mem_type and unit.get("fact_type") != mem_type:
                        continue
                    if q and q.lower() not in (unit.get("text") or "").lower():
                        continue
                    created = unit.get("created_at") or ""
                    if start and created < start:
                        continue
                    if end and created >= end:
                        continue
                    filtered.append(unit)
                filtered.sort(key=lambda u: u.get("mentioned_at") or u.get("created_at") or "", reverse=True)
                page = filtered[offset : offset + limit]
                self._send(200, {"items": page, "total": len(filtered)})
                return

            mid_match = re.match(r"^/(?P<id>[^/]+)$", rest)
            if mid_match:
                mid = mid_match.group("id")
                with state._lock:
                    unit = state.memories.get(mid)
                if not unit:
                    self._send(404, {"detail": f"memory {mid} not found"})
                    return
                self._send(200, unit)
                return

            self._send(404, {"detail": "not found"})

        def do_PATCH(self) -> None:  # noqa: N802
            if state.down:
                self.close_connection = True
                return
            if not self._check_auth():
                return
            m = self._bank_paths()
            if not m or m.group("bank") != state.bank_id:
                self._send(404, {"detail": "not found"})
                return
            rest = m.group("rest") or ""
            mid_match = re.match(r"^/(?P<id>[^/]+)$", rest)
            if not mid_match:
                self._send(404, {"detail": "not found"})
                return
            mid = mid_match.group("id")
            body = self._read_json()
            with state._lock:
                state.patch_calls.append({"id": mid, "body": body})
                if mid in state.gone_ids:
                    self._send(404, {"detail": f"memory {mid} not found"})
                    return
                if mid in state.fail_ids:
                    self._send(500, {"detail": "simulated failure"})
                    return
                unit = state.memories.get(mid)
                if not unit:
                    self._send(404, {"detail": f"memory {mid} not found"})
                    return
                if unit.get("fact_type") == "observation" and "state" in body:
                    self._send(400, {"detail": "observations cannot be curated"})
                    return
                if "state" in body:
                    unit["state"] = body["state"]
                if "reason" in body:
                    unit["reason"] = body["reason"]
                if "text" in body:
                    unit["text"] = body["text"]
            self._send(200, unit)

        def do_POST(self) -> None:  # noqa: N802
            if state.down:
                self.close_connection = True
                return
            if not self._check_auth():
                return
            m = self._bank_paths()
            if not m or m.group("bank") != state.bank_id:
                self._send(404, {"detail": "not found"})
                return
            rest = m.group("rest") or ""
            if rest not in ("", "/"):
                self._send(404, {"detail": "not found"})
                return
            body = self._read_json()
            with state._lock:
                state.retain_calls.append(body)
                created = []
                for item in body.get("items") or []:
                    mid = f"mem_{uuid.uuid4().hex[:8]}"
                    unit = {
                        "id": mid,
                        "text": item.get("content") or item.get("text") or "",
                        "fact_type": "world",
                        "state": "valid",
                        "created_at": "2026-11-06T00:00:00Z",
                        "mentioned_at": "2026-11-06T00:00:00Z",
                        "context": item.get("context") or "",
                        "tags": item.get("tags") or [],
                    }
                    state.memories[mid] = unit
                    created.append(mid)
            self._send(
                200,
                {
                    "success": True,
                    "bank_id": state.bank_id,
                    "items_count": len(created),
                    "memory_ids": created,
                    "async": False,
                },
            )

    return Handler


class FakeHindsightServer:
    def __init__(self, state: FakeHindsightState, host: str = "127.0.0.1", port: int = 0):
        self.state = state
        self._httpd = HTTPServer((host, port), make_handler(state))
        self.host, self.port = self._httpd.server_address[:2]
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "FakeHindsightServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)
