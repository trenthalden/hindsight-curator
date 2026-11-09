#!/usr/bin/env python3
"""Standalone stdlib fake of the Hindsight HTTP endpoints this plugin uses.

Reuses the in-test fake (tests/fake_hindsight.py), adds:
  - ``--port`` (default 0 = ephemeral)
  - persistent JSON state file (``--state``)
  - ``POST /__seed`` to load fixed-id memories (for demos / live eval)
  - ``GET /__state`` dump for graders

Example:
  python scripts/fake_hindsight.py --port 8899 --bank demo-curator \\
      --state /tmp/fake-hindsight.json
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from fake_hindsight import FakeHindsightServer, FakeHindsightState, make_handler  # noqa: E402


def load_state(path: Path, bank_id: str) -> FakeHindsightState:
    state = FakeHindsightState(api_key=None, bank_id=bank_id)
    if not path.exists():
        return state
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    if isinstance(raw, dict):
        if raw.get("bank_id"):
            state.bank_id = str(raw["bank_id"])
        mems = raw.get("memories") or {}
        if isinstance(mems, dict):
            for mid, unit in mems.items():
                if isinstance(unit, dict):
                    state.memories[str(mid)] = dict(unit)
                    state.memories[str(mid)]["id"] = str(mid)
        elif isinstance(mems, list):
            state.seed([m for m in mems if isinstance(m, dict)])
    return state


def save_state(path: Path, state: FakeHindsightState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with state._lock:
        payload = {
            "bank_id": state.bank_id,
            "memories": state.memories,
            "patch_calls": list(state.patch_calls),
            "retain_calls": list(state.retain_calls),
        }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def make_persistent_handler(state: FakeHindsightState, state_path: Optional[Path]):
    """Wrap the test handler with /__seed, /__state, and optional persistence."""
    Base = make_handler(state)

    class Handler(Base):  # type: ignore[valid-type,misc]
        def _persist(self) -> None:
            if state_path is not None:
                try:
                    save_state(state_path, state)
                except OSError:
                    pass

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/__state":
                with state._lock:
                    body = {
                        "ok": True,
                        "bank_id": state.bank_id,
                        "memories": state.memories,
                        "patch_calls": list(state.patch_calls),
                        "retain_calls": list(state.retain_calls),
                    }
                self._send(200, body)
                return
            if path in ("/__health", "/health"):
                self._send(200, {"ok": True, "bank_id": state.bank_id})
                return
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/__seed":
                body = self._read_json()
                if not isinstance(body, dict):
                    self._send(400, {"detail": "expected JSON object"})
                    return
                if body.get("bank_id"):
                    state.bank_id = str(body["bank_id"])
                mems = body.get("memories") or []
                if not isinstance(mems, list):
                    self._send(400, {"detail": "memories must be a list"})
                    return
                if body.get("replace"):
                    with state._lock:
                        state.memories.clear()
                        state.patch_calls.clear()
                        state.retain_calls.clear()
                state.seed([m for m in mems if isinstance(m, dict)])
                self._persist()
                self._send(200, {"ok": True, "count": len(mems), "bank_id": state.bank_id})
                return
            super().do_POST()
            self._persist()

        def do_PATCH(self) -> None:  # noqa: N802
            super().do_PATCH()
            self._persist()

    return Handler


class PersistentFakeServer:
    def __init__(
        self,
        state: FakeHindsightState,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        state_path: Optional[Path] = None,
    ):
        self.state = state
        self.state_path = state_path
        self._httpd = HTTPServer((host, port), make_persistent_handler(state, state_path))
        self.host, self.port = self._httpd.server_address[:2]
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "PersistentFakeServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)
        if self.state_path is not None:
            save_state(self.state_path, self.state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="TCP port (0 = ephemeral)")
    parser.add_argument("--bank", default="demo-curator", help="Default bank id")
    parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Persistent JSON state file (loaded on start, saved on writes)",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Block until Ctrl-C (default). Pass --once-print to only print the URL.",
    )
    args = parser.parse_args(argv)

    state_path = Path(args.state) if args.state else None
    state = load_state(state_path, args.bank) if state_path else FakeHindsightState(bank_id=args.bank)
    if not state_path:
        state.bank_id = args.bank

    server = PersistentFakeServer(state, host=args.host, port=args.port, state_path=state_path)
    server.start()
    print(f"fake_hindsight listening on {server.base_url} bank={state.bank_id}", flush=True)
    if state_path:
        print(f"state file: {state_path}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nshutting down…", flush=True)
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
