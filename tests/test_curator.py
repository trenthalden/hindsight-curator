"""Unit tests against the in-thread fake Hindsight server."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from fake_hindsight import FakeHindsightServer, FakeHindsightState  # noqa: E402
from support import load_plugin_package  # noqa: E402


class CuratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = load_plugin_package()
        self.curator_mod = sys.modules[f"{self.plugin.__name__}.curator"]
        self.journal_mod = sys.modules[f"{self.plugin.__name__}.journal"]
        self.client_mod = sys.modules[f"{self.plugin.__name__}.client"]

        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.state = FakeHindsightState(api_key=None, bank_id="demo")
        self.state.seed(
            [
                {
                    "id": "m_stale_window",
                    "text": "The context window is 192K tokens.",
                    "fact_type": "world",
                    "created_at": "2026-09-01T00:00:00Z",
                },
                {
                    "id": "m_stale_model",
                    "text": "The agent uses model X for daily work.",
                    "fact_type": "world",
                    "created_at": "2026-09-02T00:00:00Z",
                },
                {
                    "id": "m_keep_pref",
                    "text": "Prefer concise answers with sources.",
                    "fact_type": "experience",
                    "created_at": "2026-08-01T00:00:00Z",
                },
                {
                    "id": "m_other",
                    "text": "Deployments run on Tuesday evenings.",
                    "fact_type": "world",
                    "created_at": "2026-10-01T00:00:00Z",
                },
                {
                    "id": "m_obs",
                    "text": "Observation about context window habits.",
                    "fact_type": "observation",
                    "created_at": "2026-09-03T00:00:00Z",
                },
            ]
        )
        self.server = FakeHindsightServer(self.state).start()
        self.journal = self.journal_mod.Journal(self.data_dir / "curator.db")
        self.client = self.client_mod.HindsightClient(
            base_url=self.server.base_url, bank_id="demo", api_key="", timeout=5.0
        )
        self.curator = self.curator_mod.Curator(client=self.client, journal=self.journal)

    def tearDown(self) -> None:
        self.journal.close()
        self.server.stop()
        self.tmp.cleanup()

    def test_scan_happy_path(self) -> None:
        out = self.curator.scan("context window")
        self.assertTrue(out["ok"])
        ids = {c["id"] for c in out["candidates"]}
        self.assertIn("m_stale_window", ids)
        self.assertIn("m_obs", ids)
        self.assertNotIn("m_other", ids)
        for c in out["candidates"]:
            self.assertIn("excerpt", c)
            self.assertIn("date", c)
            self.assertIn("type", c)

    def test_scan_type_filter(self) -> None:
        out = self.curator.scan("context", types="world")
        ids = {c["id"] for c in out["candidates"]}
        self.assertEqual(ids, {"m_stale_window"})

    def test_scan_regex(self) -> None:
        out = self.curator.scan(r"/model X/i")
        ids = {c["id"] for c in out["candidates"]}
        self.assertEqual(ids, {"m_stale_model"})

    def test_plan_is_dry_run(self) -> None:
        self.state.reset_counters()
        out = self.curator.plan(query="context window", reason="2026-11 curation: window changed")
        self.assertTrue(out["ok"])
        self.assertTrue(out["dry_run"])
        self.assertTrue(out["plan_id"].startswith("plan_"))
        self.assertEqual(self.state.patch_calls, [])

    def test_whitelist_respected(self) -> None:
        self.journal.add_whitelist(ids=["m_stale_window"], note="still true for legacy stack")
        out = self.curator.plan(
            candidate_ids=["m_stale_window", "m_stale_model"],
            reason="retire stale",
        )
        self.assertTrue(out["ok"])
        self.assertIn("m_stale_window", out["kept_ids"])
        self.assertEqual(out["to_invalidate"], 1)
        applied = self.curator.apply(out["plan_id"])
        self.assertTrue(applied["ok"])
        patched_ids = {c["id"] for c in self.state.patch_calls}
        self.assertNotIn("m_stale_window", patched_ids)
        self.assertIn("m_stale_model", patched_ids)
        self.assertEqual(self.state.memories["m_stale_window"]["state"], "valid")
        self.assertEqual(self.state.memories["m_stale_model"]["state"], "invalidated")

    def test_keep_param_whitelists(self) -> None:
        out = self.curator.plan(
            query="model",
            reason="model upgrade",
            keep=["m_stale_model"],
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["to_invalidate"], 0)
        self.assertIn("m_stale_model", out["kept_ids"])

    def test_apply_and_restore_roundtrip(self) -> None:
        plan = self.curator.plan(query="model X", reason="model upgrade 2026-11")
        self.assertTrue(plan["ok"])
        self.assertGreaterEqual(plan["to_invalidate"], 1)
        applied = self.curator.apply(plan["plan_id"])
        self.assertTrue(applied["ok"])
        self.assertGreaterEqual(applied["invalidated"], 1)
        self.assertFalse(applied.get("subset"))
        self.assertEqual(self.state.memories["m_stale_model"]["state"], "invalidated")

        restored = self.curator.restore(plan_id=plan["plan_id"])
        self.assertTrue(restored["ok"])
        self.assertGreaterEqual(restored["restored"], 1)
        self.assertEqual(self.state.memories["m_stale_model"]["state"], "valid")

    def test_apply_subset_ids(self) -> None:
        """curate_apply(plan_id, ids?) invalidates only the selected non-kept targets."""
        plan = self.curator.plan(
            candidate_ids=["m_stale_window", "m_stale_model", "m_other"],
            reason="subset apply demo",
            keep=["m_other"],
        )
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["to_invalidate"], 2)
        plan_id = plan["plan_id"]

        applied = self.curator.apply(plan_id, ids=["m_stale_window"])
        self.assertTrue(applied["ok"], applied)
        self.assertTrue(applied.get("subset"))
        self.assertEqual(applied["invalidated"], 1)
        by_id = {r["id"]: r["result"] for r in applied["results"]}
        self.assertEqual(by_id, {"m_stale_window": "ok"})
        self.assertEqual(self.state.memories["m_stale_window"]["state"], "invalidated")
        self.assertEqual(self.state.memories["m_stale_model"]["state"], "valid")
        self.assertEqual(self.state.memories["m_other"]["state"], "valid")

        # Kept / whitelist ids are ignored even if requested.
        plan2 = self.curator.plan(
            candidate_ids=["m_stale_model", "m_keep_pref"],
            reason="subset keep guard",
            keep=["m_keep_pref"],
        )
        bad = self.curator.apply(plan2["plan_id"], ids=["m_keep_pref"])
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["kind"], "validation")
        self.assertEqual(self.state.memories["m_keep_pref"]["state"], "valid")

        # Omitting ids still applies every non-kept target (backward compatible).
        full = self.curator.apply(plan2["plan_id"])
        self.assertTrue(full["ok"], full)
        self.assertFalse(full.get("subset"))
        self.assertEqual(self.state.memories["m_stale_model"]["state"], "invalidated")
        self.assertEqual(self.state.memories["m_keep_pref"]["state"], "valid")

    def test_list_and_get_plan(self) -> None:
        plan = self.curator.plan(query="Tuesday", reason="schedule review", keep=["m_keep_pref"])
        self.assertTrue(plan["ok"])
        listed = self.curator.list_plans()
        self.assertTrue(listed["ok"])
        self.assertTrue(any(p["plan_id"] == plan["plan_id"] for p in listed["plans"]))
        detail = self.curator.get_plan(plan["plan_id"])
        self.assertTrue(detail["ok"])
        locked = {i["id"]: i["locked"] for i in detail["items"]}
        self.assertTrue(any(not v for v in locked.values()))

    def test_apply_partial_failure(self) -> None:
        self.state.fail_ids.add("m_stale_model")
        self.state.gone_ids.add("m_stale_window")
        plan = self.curator.plan(
            candidate_ids=["m_stale_model", "m_stale_window", "m_other"],
            reason="bulk retire",
        )
        self.assertTrue(plan["ok"])
        applied = self.curator.apply(plan["plan_id"])
        self.assertTrue(applied["ok"])
        by_id = {r["id"]: r["result"] for r in applied["results"]}
        self.assertEqual(by_id["m_stale_model"], "failed")
        self.assertEqual(by_id["m_stale_window"], "not_found")
        self.assertEqual(by_id["m_other"], "ok")
        self.assertEqual(self.state.memories["m_other"]["state"], "invalidated")
        self.assertEqual(self.state.memories["m_stale_model"]["state"], "valid")

    def test_404_on_missing_memory(self) -> None:
        plan = self.curator.plan(candidate_ids=["does-not-exist"], reason="noop")
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["total_candidates"], 0)

    def test_auth_error(self) -> None:
        self.server.stop()
        locked = FakeHindsightState(api_key="secret-token", bank_id="demo")
        locked.seed([{"id": "m1", "text": "secret bank fact about widgets", "fact_type": "world"}])
        srv = FakeHindsightServer(locked).start()
        self.addCleanup(srv.stop)

        journal = self.journal_mod.Journal(self.data_dir / "auth.db")
        self.addCleanup(journal.close)
        bad = self.curator_mod.Curator(
            client=self.client_mod.HindsightClient(
                base_url=srv.base_url, bank_id="demo", api_key="wrong"
            ),
            journal=journal,
        )
        out = bad.scan("widgets")
        self.assertFalse(out["ok"])
        self.assertEqual(out["kind"], "auth")

        good = self.curator_mod.Curator(
            client=self.client_mod.HindsightClient(
                base_url=srv.base_url, bank_id="demo", api_key="secret-token"
            ),
            journal=journal,
        )
        ok = good.scan("widgets")
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["count"], 1)

    def test_server_down_clean_error(self) -> None:
        self.server.stop()
        out = self.curator.scan("anything")
        self.assertFalse(out["ok"])
        self.assertIn(out.get("kind"), {"unreachable", "timeout", "bad_response", "http", "error"})

    def test_checkpoint(self) -> None:
        out = self.curator.checkpoint(
            "Current context window is 1M tokens; model Y is the daily default.",
            context="post-curation checkpoint",
        )
        self.assertTrue(out["ok"])
        self.assertTrue(out["retained"])
        self.assertEqual(len(self.state.retain_calls), 1)
        item = self.state.retain_calls[0]["items"][0]
        self.assertIn("state:current", item["tags"])

    def test_history(self) -> None:
        plan = self.curator.plan(query="Tuesday", reason="schedule changed")
        self.curator.apply(plan["plan_id"])
        hist = self.curator.history(limit=10)
        self.assertTrue(hist["ok"])
        actions = [h["action"] for h in hist["items"]]
        self.assertIn("plan", actions)
        self.assertIn("apply", actions)


if __name__ == "__main__":
    unittest.main()
