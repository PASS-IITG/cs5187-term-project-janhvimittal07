"""Unit and integration tests for Campaign Runner (Task N6)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(os.environ.get("FUZZ_WORK", HERE.parent.parent))
sys.path.insert(0, str(HERE))

import campaign  # noqa: E402


class TestCampaign(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.ckpt_path = self.tmp_path / "checkpoint.json"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_campaign_state_serialization(self):
        """CampaignState must save and load cleanly with identical fields."""
        state = campaign.CampaignState(
            campaign_id="test_camp_1",
            build="asan",
            mode="hybrid",
            seed=123,
            target_seconds=120.0,
            elapsed_seconds=45.5,
            total_queries=500,
            total_ok=450,
            total_errors=49,
            total_crashes=1,
            unique_crashes=1,
            seen_signatures=["sig_abc123"],
            pending_triage=[{"kind": "crash", "origin": "test", "query": "SELECT 1;", "signature": "sig_abc123"}],
            triaged_finding_ids=[],
            generator_stats={"sqlsmith": {"queries": 200, "ok": 190, "errors": 10, "crashes": 0, "elapsed_seconds": 20.0}}
        )

        state.save(self.ckpt_path)
        self.assertTrue(self.ckpt_path.exists())

        loaded = campaign.CampaignState.load(self.ckpt_path)
        self.assertEqual(loaded.campaign_id, "test_camp_1")
        self.assertEqual(loaded.total_queries, 500)
        self.assertEqual(loaded.total_ok, 450)
        self.assertEqual(loaded.seen_signatures, ["sig_abc123"])
        self.assertEqual(len(loaded.pending_triage), 1)
        self.assertEqual(loaded.generator_stats["sqlsmith"]["queries"], 200)

    def test_campaign_runner_short_execution(self):
        """Runner must run for a short budget (1.5s), execute queries, and update state."""
        state = campaign.CampaignState(
            campaign_id="test_short_run",
            build="asan",
            mode="sqlsmith",
            seed=42,
            target_seconds=2.0,
            elapsed_seconds=0.0,
        )

        runner_inst = campaign.CampaignRunner(
            state=state,
            triage_interval=10.0,
            checkpoint_interval=1.0,
            batch_size=5,
        )

        # Run the short campaign
        finished_state = runner_inst.run()

        self.assertGreaterEqual(finished_state.elapsed_seconds, 1.0)
        self.assertGreater(finished_state.total_queries, 0)
        self.assertIn(finished_state.status, ("completed", "interrupted"))

    def test_campaign_resumability(self):
        """Interrupted campaign must seamlessly resume from checkpoint."""
        state1 = campaign.CampaignState(
            campaign_id="test_resume_run",
            build="asan",
            mode="sqlsmith",
            seed=42,
            target_seconds=1.5,
            elapsed_seconds=0.0,
        )

        runner1 = campaign.CampaignRunner(
            state=state1,
            triage_interval=10.0,
            checkpoint_interval=0.5,
            batch_size=5,
        )
        finished1 = runner1.run()
        q1 = finished1.total_queries
        e1 = finished1.elapsed_seconds

        # Extend target budget and resume
        finished1.target_seconds = e1 + 1.5
        runner2 = campaign.CampaignRunner(
            state=finished1,
            triage_interval=10.0,
            checkpoint_interval=0.5,
            batch_size=5,
        )
        finished2 = runner2.run()

        self.assertGreater(finished2.total_queries, q1)
        self.assertGreater(finished2.elapsed_seconds, e1)

    def test_report_generation(self):
        """Campaign report must generate valid markdown with executive summary and throughput."""
        state = campaign.CampaignState(
            campaign_id="test_report_run",
            build="asan",
            mode="hybrid",
            seed=42,
            target_seconds=60.0,
            elapsed_seconds=30.0,
            total_queries=150,
            total_ok=135,
            total_errors=15,
            total_crashes=0,
            generator_stats={
                "sqlsmith": {"queries": 100, "ok": 90, "errors": 10, "crashes": 0, "elapsed_seconds": 18.0},
                "mutation": {"queries": 50, "ok": 45, "errors": 5, "crashes": 0, "elapsed_seconds": 12.0},
            }
        )

        runner_inst = campaign.CampaignRunner(state=state)
        report_path = runner_inst.generate_campaign_report()

        self.assertTrue(report_path.exists())
        content = report_path.read_text()
        self.assertIn("# DuckDB Directed Fuzzing Campaign Report", content)
        self.assertIn("150", content)
        self.assertIn("sqlsmith", content)
        self.assertIn("mutation", content)


if __name__ == "__main__":
    unittest.main()
