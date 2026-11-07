"""Stdlib urllib client for the Hindsight public HTTP API (list / PATCH / retain)."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Optional


USER_AGENT = "hindsight-curator/0.1"


class HindsightError(Exception):
    """Clean, user-facing failure talking to Hindsight."""

    def __init__(self, message: str, *, status: Optional[int] = None, kind: str = "error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.kind = kind

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": False, "error": self.message, "kind": self.kind}
        if self.status is not None:
            out["status"] = self.status
        return out


@dataclass
class HindsightClient:
    """Thin HTTP wrapper. Network calls go only to ``base_url``."""

    base_url: str
    bank_id: str
    api_key: str = ""
    timeout: float = 30.0

    def _url(self, path: str, query: Optional[Mapping[str, Any]] = None) -> str:
        base = self.base_url.rstrip("/")
        url = f"{base}{path}"
        if query:
            pairs = []
            for key, value in query.items():
                if value is None or value == "":
                    continue
                if isinstance(value, (list, tuple)):
                    for item in value:
                        pairs.append((key, str(item)))
                else:
                    pairs.append((key, str(value)))
            if pairs:
                url = f"{url}?{urllib.parse.urlencode(pairs)}"
        return url

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        key = (self.api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        url = self._url(path, query)
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 — URL from plugin config
                raw = resp.read()
                if not raw:
                    return {}
                try:
                    return json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    raise HindsightError(
                        f"Hindsight returned non-JSON ({resp.status})",
                        status=getattr(resp, "status", None),
                        kind="bad_response",
                    ) from exc
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                payload = exc.read().decode("utf-8", errors="replace")
                detail = payload[:400]
                try:
                    parsed = json.loads(payload)
                    if isinstance(parsed, dict):
                        detail = str(parsed.get("detail") or parsed.get("error") or parsed.get("message") or detail)
                except json.JSONDecodeError:
                    pass
            except Exception:
                pass
            if exc.code in (401, 403):
                raise HindsightError(
                    f"Hindsight auth failed ({exc.code}): {detail or 'unauthorized'}",
                    status=exc.code,
                    kind="auth",
                ) from exc
            if exc.code == 404:
                raise HindsightError(
                    f"Not found: {detail or path}",
                    status=404,
                    kind="not_found",
                ) from exc
            raise HindsightError(
                f"Hindsight HTTP {exc.code}: {detail or exc.reason}",
                status=exc.code,
                kind="http",
            ) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise HindsightError(
                f"Hindsight server unreachable at {self.base_url}: {reason}",
                kind="unreachable",
            ) from exc
        except TimeoutError as exc:
            raise HindsightError(
                f"Hindsight request timed out talking to {self.base_url}",
                kind="timeout",
            ) from exc
        except socket.timeout as exc:
            raise HindsightError(
                f"Hindsight request timed out talking to {self.base_url}",
                kind="timeout",
            ) from exc

    def list_memories(
        self,
        *,
        q: Optional[str] = None,
        state: Optional[str] = "valid",
        types: Optional[list[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {
            "limit": max(1, min(int(limit), 500)),
            "offset": max(0, int(offset)),
        }
        if q:
            query["q"] = q
        if state:
            query["state"] = state
        if start_date:
            query["start_date"] = start_date
        if end_date:
            query["end_date"] = end_date
        # Server accepts a single type=; we filter multi-type client-side when needed.
        if types and len(types) == 1:
            query["type"] = types[0]
        path = f"/v1/default/banks/{urllib.parse.quote(self.bank_id, safe='')}/memories/list"
        return self.request("GET", path, query=query)

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        path = (
            f"/v1/default/banks/{urllib.parse.quote(self.bank_id, safe='')}"
            f"/memories/{urllib.parse.quote(memory_id, safe='')}"
        )
        return self.request("GET", path)

    def patch_memory(self, memory_id: str, payload: Mapping[str, Any]) -> Any:
        path = (
            f"/v1/default/banks/{urllib.parse.quote(self.bank_id, safe='')}"
            f"/memories/{urllib.parse.quote(memory_id, safe='')}"
        )
        return self.request("PATCH", path, body=dict(payload))

    def retain(self, text: str, *, context: Optional[str] = None, tags: Optional[list[str]] = None) -> Any:
        item: dict[str, Any] = {"content": text}
        if context:
            item["context"] = context
        if tags:
            item["tags"] = list(tags)
        path = f"/v1/default/banks/{urllib.parse.quote(self.bank_id, safe='')}/memories"
        return self.request("POST", path, body={"items": [item]})
