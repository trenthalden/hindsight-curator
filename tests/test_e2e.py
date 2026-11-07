"""End-to-end check through the real registered tool handlers against the fake server."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from fake_hindsight import FakeHindsightServer, FakeHindsightState  # noqa: E402
from support import load_plugin_package  # noqa: E402


class _FakeCtx:
    """Minimal PluginContext stand-in that records tool/command registrations."""

    def __init__(self, settings: dict[str, Any]):
        self._settings = settings
        self.tools: dict[str, Any] = {}
        self.commands: dict[str, Any] = {}

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def register_tool(self, name: str, toolset: str = "", schema=None, handler=None, **_kwargs) -> None:
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_command(self, name: str, handler=None, description: str = "", args_hint: str = "", **_kwargs) -> None:
        self.commands[name] = {"handler": handler, "description": description, "args_hint": args_hint}


class E2EToolHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = load_plugin_package()
        self.plugin.reset_curator()

        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

        self.state = FakeHindsightState(api_key=None, bank_id="demo")
        self.state.seed(
            [
                {
                    "id": "e2e_stale_192k",
                    "text": "The context window is 192K tokens.",
                    "fact_type": "world",
                    "created_at": "2026-09-01T00:00:00Z",
                },
                {
                    "id": "e2e_stale_model",
                    "text": "Daily work uses model X.",
                    "fact_type": "world",
                    "created_at": "2026-09-02T00:00:00Z",
                },
                {
                    "id": "e2e_keep",
                    "text": "Owner prefers model X only for offline drafts.",
                    "fact_type": "experience",
                    "created_at": "2026-09-02T12:00:00Z",
                },
            ]
        )
        self.server = FakeHindsightServer(self.state).start()

        # Point the plugin at the fake server + temp data dir.
        self.plugin.configure_for_tests(
            data_dir=self.data_dir,
            api_url=self.server.base_url,
            bank_id="demo",
            api_key="",
        )

        self.ctx = _FakeCtx({"api_url": self.server.base_url, "bank_id": "demo"})
        self.plugin.register(self.ctx)
        self.assertIn("curate_scan", self.ctx.tools)
        self.assertIn("memory-curate", self.ctx.commands)

    def tearDown(self) -> None:
        self.plugin.reset_curator()
        self.server.stop()
        self.tmp.cleanup()

    def _call(self, name: str, args: dict) -> dict:
        raw = self.ctx.tools[name]["handler"](args)
        return json.loads(raw)

    def test_tool_handlers_full_flow(self) -> None:
        scan = self._call("curate_scan", {"query": "192K", "limit": 20})
        self.assertTrue(scan["ok"], scan)
        self.assertGreaterEqual(scan["count"], 1)
        stale_ids = [c["id"] for c in scan["candidates"]]
        self.assertIn("e2e_stale_192k", stale_ids)

        plan = self._call(
            "curate_plan",
            {
                "query": "model X",
                "reason": "2026-11-06 curation: model upgraded",
                "keep": ["e2e_keep"],
            },
        )
        self.assertTrue(plan["ok"], plan)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(self.state.patch_calls, [])
        self.assertIn("e2e_keep", plan["kept_ids"])
        self.assertGreaterEqual(plan["to_invalidate"], 1)
        plan_id = plan["plan_id"]

        applied = self._call("curate_apply", {"plan_id": plan_id})
        self.assertTrue(applied["ok"], applied)
        self.assertGreaterEqual(applied["invalidated"], 1)
        self.assertEqual(self.state.memories["e2e_stale_model"]["state"], "invalidated")
        self.assertEqual(self.state.memories["e2e_keep"]["state"], "valid")

        ck = self._call(
            "curate_checkpoint",
            {
                "text": "Daily work uses model Y. Context window is 1M tokens.",
                "context": "demo checkpoint",
            },
        )
        self.assertTrue(ck["ok"], ck)
        self.assertEqual(len(self.state.retain_calls), 1)

        hist = self._call("curate_history", {"limit": 10})
        self.assertTrue(hist["ok"])
        actions = {h["action"] for h in hist["items"]}
        self.assertTrue({"plan", "apply", "checkpoint"} <= actions)

        restored = self._call("curate_restore", {"plan_id": plan_id})
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(self.state.memories["e2e_stale_model"]["state"], "valid")

    def test_slash_command_scan_and_history(self) -> None:
        handler = self.ctx.commands["memory-curate"]["handler"]
        out = handler("scan 192K --limit 5")
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["count"], 1)

        plan_out = handler('plan "model X" --reason "upgrade" --keep e2e_keep')
        plan = json.loads(plan_out)
        self.assertTrue(plan["ok"], plan)
        apply_out = handler(f"apply {plan['plan_id']}")
        applied = json.loads(apply_out)
        self.assertTrue(applied["ok"])

        hist_out = handler("history 5")
        hist = json.loads(hist_out)
        self.assertTrue(hist["ok"])


if __name__ == "__main__":
    unittest.main()
