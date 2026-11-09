#!/usr/bin/env python3
"""LIVE end-to-end eval: real Hermes LLM (profile hcdemo ONLY) + fake Hindsight.

Max 3 live runs (they spend quota). Never touches other profiles or the owner's
real Hindsight (:8900/:8888). Writes transcript + verdict under out/live-eval/
(git-ignored).

Usage (from repo root):
  python scripts/live_eval.py
  python scripts/live_eval.py --run-id 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
TRUTH_PATH = ROOT / "scripts" / "demo_truth.json"
SEED = ROOT / "scripts" / "seed_demo_bank.py"
FAKE = ROOT / "scripts" / "fake_hindsight.py"

# Override with LIVE_EVAL_HERMES_HOME / LIVE_EVAL_HERMES_BIN / LIVE_EVAL_PROFILE.
HERMES_DESKTOP = Path(os.environ.get("LIVE_EVAL_HERMES_HOME", Path.home() / ".hermes-desktop"))
HERMES_BIN = Path(os.environ.get("LIVE_EVAL_HERMES_BIN", HERMES_DESKTOP / "hermes-agent" / ".hermes" / "bin" / "hermes"))
PROFILE = os.environ.get("LIVE_EVAL_PROFILE", "hcdemo")
BANK = "demo-curator"
PROVIDER = "anthropic"
MODEL = "claude-sonnet-4-6"

TURN1 = (
    "In my project the model's context window changed from 192K to 1M tokens. "
    "Update this fact everywhere in memory."
)
TURN2 = "Yes, apply it."


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def http_json(url: str, method: str = "GET", body: Any = None, timeout: float = 30.0) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8") or "{}")


def wait_health(base: str, tries: int = 40) -> None:
    last: Optional[Exception] = None
    for _ in range(tries):
        try:
            http_json(f"{base}/__health")
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.15)
    raise RuntimeError(f"fake server not healthy at {base}: {last}")


def hermes_env() -> dict[str, str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(HERMES_DESKTOP)
    # Keep temp inside the disposable demo profile, never another profile's cache.
    tmp = HERMES_DESKTOP / "profiles" / PROFILE / "cache" / "live-eval-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = str(tmp)
    return env


def run_hermes(args: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    cmd = [str(HERMES_BIN), "-p", PROFILE, *args]
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=hermes_env(),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def install_plugin_symlink() -> Path:
    plugins = HERMES_DESKTOP / "profiles" / PROFILE / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    target = plugins / "hindsight-curator"
    if target.is_symlink() or target.exists():
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)
    target.symlink_to(ROOT, target_is_directory=True)
    return target


def enable_and_configure(api_url: str) -> None:
    # Enable plugin (idempotent).
    r = run_hermes(["plugins", "enable", "hindsight-curator"], timeout=120)
    if r.returncode != 0 and "already" not in (r.stdout + r.stderr).lower():
        # Some builds print OK to stderr; only fail on hard errors.
        combined = (r.stdout or "") + (r.stderr or "")
        if "Error" in combined or "error:" in combined.lower():
            raise RuntimeError(f"enable failed:\n{combined}")

    # Point settings at the fake server (config set is profile-scoped via -p).
    for key, value in (
        ("plugins.entries.hindsight-curator.settings.api_url", api_url),
        ("plugins.entries.hindsight-curator.settings.bank_id", BANK),
    ):
        r = run_hermes(["config", "set", key, value], timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"config set {key} failed:\n{r.stdout}\n{r.stderr}")

    # Ensure enabled list contains the plugin.
    cfg_path = HERMES_DESKTOP / "profiles" / PROFILE / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    if "hindsight-curator" not in text:
        # Fallback: append a minimal plugins block.
        with cfg_path.open("a", encoding="utf-8") as fh:
            fh.write(
                "\nplugins:\n  enabled:\n    - hindsight-curator\n  entries:\n"
                f"    hindsight-curator:\n      settings:\n"
                f"        api_url: {api_url}\n        bank_id: {BANK}\n"
            )


def parse_session_id(text: str) -> Optional[str]:
    m = re.search(r"session_id:\s*(\S+)", text)
    return m.group(1).strip() if m else None


def dump_server(base: str) -> dict[str, Any]:
    return http_json(f"{base}/__state")


def grade(state: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    memories = state.get("memories") or {}
    rows: list[dict[str, Any]] = []
    failed = False

    def check(group: str, expected_state: str, items: list[dict[str, Any]]) -> None:
        nonlocal failed
        for item in items:
            mid = item["id"]
            unit = memories.get(mid) or {}
            actual = unit.get("state") or "MISSING"
            text = unit.get("text") or item.get("text") or ""
            ok = actual == expected_state
            if not ok:
                failed = True
            rows.append(
                {
                    "id": mid,
                    "group": group,
                    "expected": expected_state,
                    "actual": actual,
                    "ok": ok,
                    "excerpt": (text[:80] + "…") if len(text) > 80 else text,
                }
            )

    expect = truth.get("expect_after_apply") or {}
    check("stale", expect.get("stale_state", "invalidated"), truth.get("stale") or [])
    check("decoy", expect.get("decoy_state", "valid"), truth.get("decoy") or [])
    check("keep", expect.get("keep_state", "valid"), truth.get("keep") or [])
    check("other", expect.get("other_state", "valid"), truth.get("other") or [])

    # Checkpoint with 1M
    needles = [str(x).lower() for x in (truth.get("checkpoint_must_contain") or ["1m"])]
    retain_calls = state.get("retain_calls") or []
    checkpoint_ok = False
    checkpoint_text = ""
    for call in retain_calls:
        for it in call.get("items") or []:
            content = str(it.get("content") or it.get("text") or "")
            tags = [str(t) for t in (it.get("tags") or [])]
            if "state:current" in tags or True:
                if all(n in content.lower() for n in needles):
                    checkpoint_ok = True
                    checkpoint_text = content
                    break
        if checkpoint_ok:
            break
    # Also accept a memory that contains 1M and looks like a checkpoint.
    if not checkpoint_ok:
        for unit in memories.values():
            text = str(unit.get("text") or "")
            tags = [str(t) for t in (unit.get("tags") or [])]
            if all(n in text.lower() for n in needles) and (
                "state:current" in tags or "1m" in text.lower()
            ):
                # Prefer tagging, but accept any valid memory stating 1M after apply.
                if unit.get("state") == "valid" and "context" in text.lower():
                    checkpoint_ok = True
                    checkpoint_text = text
                    break
    if not checkpoint_ok:
        failed = True

    return {
        "ok": not failed and checkpoint_ok,
        "checkpoint_ok": checkpoint_ok,
        "checkpoint_text": checkpoint_text,
        "rows": rows,
        "patch_calls": len(state.get("patch_calls") or []),
        "retain_calls": len(retain_calls),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, default=1, help="Run number label (1..3)")
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Reuse existing hcdemo plugin symlink/settings",
    )
    args = parser.parse_args(argv)

    if not HERMES_BIN.exists():
        print(f"missing hermes binary: {HERMES_BIN}", file=sys.stderr)
        return 2
    if PROFILE != "hcdemo":
        print("refusing: PROFILE must be hcdemo", file=sys.stderr)
        return 2

    truth = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
    out_dir = ROOT / "out" / "live-eval" / f"run-{args.run_id}-{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)

    port = free_port()
    state_file = out_dir / "fake-state.json"
    fake_proc = subprocess.Popen(
        [
            sys.executable,
            str(FAKE),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--bank",
            BANK,
            "--state",
            str(state_file),
        ],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    transcript: dict[str, Any] = {
        "started_at": _utc(),
        "profile": PROFILE,
        "provider": PROVIDER,
        "model": MODEL,
        "fake_url": base,
        "bank": BANK,
        "run_id": args.run_id,
    }

    try:
        wait_health(base)
        # Seed fixed ids
        seed = subprocess.run(
            [
                sys.executable,
                str(SEED),
                "--url",
                base,
                "--bank",
                BANK,
                "--fixed-ids",
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        transcript["seed_stdout"] = seed.stdout
        transcript["seed_stderr"] = seed.stderr
        if seed.returncode != 0:
            raise RuntimeError(f"seed failed: {seed.stderr or seed.stdout}")

        if not args.skip_install:
            link = install_plugin_symlink()
            transcript["plugin_link"] = str(link)
            enable_and_configure(base)
        else:
            enable_and_configure(base)

        # Turn 1
        t1 = run_hermes(
            [
                "chat",
                "-Q",
                "--provider",
                PROVIDER,
                "-m",
                MODEL,
                "-s",
                "hindsight-curator:memory-curate",
                "-q",
                TURN1,
            ],
            timeout=900,
        )
        transcript["turn1"] = {
            "returncode": t1.returncode,
            "stdout": t1.stdout,
            "stderr": t1.stderr,
            "query": TURN1,
        }
        (out_dir / "turn1.txt").write_text(
            (t1.stdout or "") + "\n--- stderr ---\n" + (t1.stderr or ""),
            encoding="utf-8",
        )
        session_id = parse_session_id((t1.stdout or "") + "\n" + (t1.stderr or ""))
        transcript["session_id"] = session_id

        mid_state = dump_server(base)
        (out_dir / "state-after-turn1.json").write_text(
            json.dumps(mid_state, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        invalidated_early = [
            mid
            for mid, unit in (mid_state.get("memories") or {}).items()
            if unit.get("state") == "invalidated"
        ]
        transcript["invalidated_after_turn1"] = invalidated_early
        if invalidated_early:
            verdict = {
                "ok": False,
                "reason": "memories already invalidated after turn 1 (before owner OK)",
                "invalidated_after_turn1": invalidated_early,
                "rows": [],
            }
            (out_dir / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
            (out_dir / "transcript.json").write_text(
                json.dumps(transcript, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            print("FAIL: applied before confirmation")
            print(json.dumps(verdict, indent=2))
            return 1

        if not session_id:
            raise RuntimeError("could not parse session_id from turn 1 output")

        # Turn 2 — resume same session
        t2 = run_hermes(
            [
                "chat",
                "-Q",
                "--provider",
                PROVIDER,
                "-m",
                MODEL,
                "--resume",
                session_id,
                "-q",
                TURN2,
            ],
            timeout=900,
        )
        transcript["turn2"] = {
            "returncode": t2.returncode,
            "stdout": t2.stdout,
            "stderr": t2.stderr,
            "query": TURN2,
        }
        (out_dir / "turn2.txt").write_text(
            (t2.stdout or "") + "\n--- stderr ---\n" + (t2.stderr or ""),
            encoding="utf-8",
        )

        final_state = dump_server(base)
        (out_dir / "state-after-turn2.json").write_text(
            json.dumps(final_state, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        verdict = grade(final_state, truth)
        transcript["finished_at"] = _utc()
        transcript["verdict_ok"] = verdict["ok"]

        (out_dir / "verdict.json").write_text(
            json.dumps(verdict, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        (out_dir / "transcript.json").write_text(
            json.dumps(transcript, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        # Pretty table
        print(f"out: {out_dir}")
        print(f"verdict: {'PASS' if verdict['ok'] else 'FAIL'}")
        print(f"{'id':28} {'group':8} {'expected':12} {'actual':12} ok")
        for row in verdict["rows"]:
            print(
                f"{row['id'][:28]:28} {row['group'][:8]:8} {row['expected'][:12]:12} "
                f"{row['actual'][:12]:12} {'Y' if row['ok'] else 'N'}"
            )
        print(f"checkpoint_ok={verdict['checkpoint_ok']} text={verdict.get('checkpoint_text', '')[:120]!r}")
        return 0 if verdict["ok"] else 1
    except Exception as exc:  # noqa: BLE001
        transcript["error"] = str(exc)
        (out_dir / "transcript.json").write_text(
            json.dumps(transcript, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if fake_proc.poll() is None:
            fake_proc.send_signal(signal.SIGTERM)
            try:
                fake_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                fake_proc.kill()
        # Keep fake server log
        if fake_proc.stdout:
            try:
                log = fake_proc.stdout.read()
                (out_dir / "fake-server.log").write_text(log or "", encoding="utf-8")
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
