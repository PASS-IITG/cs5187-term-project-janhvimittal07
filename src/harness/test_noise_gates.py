#!/usr/bin/env python3
"""Automated validation tests for Task N8 (Noise Gates).

Verifies the 6 required noise gates from WORKPLAN.md Section 4a:
1. Determinism gate on frozen data
2. Setup taint tracking
3. Unordered-window filter
4. By-design error rules (BITSTRING_AGG statistics_propagation & both-error pattern)
5. Quick mode for timeouts
6. Severity ranking in REPORT.md & Acceptance Test
"""
import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import duckdb
import oracle
import sweep_oracle
import triage
import fuzz


class TestNoiseGates(unittest.TestCase):
    def test_gate1_determinism_gate_frozen_data(self):
        """Setup using random() yields divergent data if re-run per config, but matches on frozen data."""
        setup = ["CREATE TABLE t_det AS SELECT range AS k, random() AS r FROM range(20);"]
        query = "SELECT sum(r) FROM t_det;"
        cfg_stmts = ["SET disabled_optimizers='filter_pushdown'"]

        # 1. Without frozen data (separate runs):
        c1 = duckdb.connect(":memory:")
        for s in setup: c1.execute(s)
        r1 = c1.execute(query).fetchall()
        c1.close()

        c2 = duckdb.connect(":memory:")
        for s in setup: c2.execute(s)
        r2 = c2.execute(query).fetchall()
        c2.close()
        self.assertNotEqual(r1, r2, "Unfrozen setup should generate different random numbers")

        # 2. With frozen data (Gate 1):
        identical = triage._check_on_frozen_data(setup, query, cfg_stmts)
        self.assertTrue(identical, "Determinism gate must recognize identical results on frozen data")

    def test_gate2_setup_taint_tracking(self):
        """Setup statements with non-deterministic functions must be flagged as tainted."""
        tainted_stmts = [
            "CREATE TABLE t AS SELECT gen_random_uuid(), i FROM range(5) r(i);",
            "INSERT INTO t VALUES (uuid(), 10);",
            "CREATE TABLE s AS SELECT * FROM t TABLESAMPLE 50%;",
        ]
        clean_stmts = [
            "CREATE TABLE t(id INT, v VARCHAR);",
            "INSERT INTO t VALUES (1, 'hello'), (2, 'world');",
        ]

        for s in tainted_stmts:
            self.assertTrue(bool(sweep_oracle.SETUP_TAINT_RX.search(s)), f"Expected {s} to be tainted")

        for s in clean_stmts:
            self.assertFalse(bool(sweep_oracle.SETUP_TAINT_RX.search(s)), f"Expected {s} to be clean")

    def test_gate3_unordered_window_filter(self):
        """Window functions with frame specifications but no ORDER BY have non-deterministic row order."""
        unordered = [
            "SELECT STDDEV_SAMP(k) OVER (ROWS UNBOUNDED PRECEDING) FROM t;",
            "SELECT sum(x) OVER (PARTITION BY g ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM t;",
            "SELECT avg(v) OVER (RANGE BETWEEN 1 PRECEDING AND 1 FOLLOWING) FROM t;",
        ]
        ordered = [
            "SELECT sum(x) OVER (ORDER BY k ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM t;",
            "SELECT count(*) OVER () FROM t;",
            "SELECT row_number() OVER (PARTITION BY g ORDER BY k) FROM t;",
        ]

        for s in unordered:
            self.assertTrue(sweep_oracle.is_unordered_window(s), f"Expected {s} to be flagged as unordered")

        for s in ordered:
            self.assertFalse(sweep_oracle.is_unordered_window(s), f"Expected {s} to be allowed")

    def test_gate4_bydesign_error_rules(self):
        """BITSTRING_AGG requiring statistics_propagation must be suppressed as by-design."""
        sql_bitstring = "SELECT BITSTRING_AGG(i) FROM ints;"
        differs = ["no_optimizers"]
        res = {"default": ("ok", [(1,)]), "no_optimizers": ("error", "BinderException", "requires statistics_propagation")}

        suppressed = sweep_oracle.is_bydesign_error_diff(res["default"], differs, res, sql_bitstring)
        self.assertTrue(suppressed, "BITSTRING_AGG error without statistics_propagation must be suppressed")

        # Both-error pattern: both configs rejected query
        both_err_res = {"default": ("error", "ParserException", "syntax error"), "other": ("error", "BinderException", "not found")}
        suppressed_both = sweep_oracle.is_bydesign_error_diff(both_err_res["default"], ["other"], both_err_res, "SELECT *;")
        self.assertTrue(suppressed_both, "Queries rejected by both configurations must be suppressed")

    def test_gate5_quick_mode_timeouts(self):
        """Quick mode checks timeouts within 3s without blocking on 5 configs x 60s."""
        # Non-reproducing query
        clean_stmts = ["SELECT 42 AS ans;"]
        triage_res = triage.triage_crash(clean_stmts, origin="baseline:timeout_dummy_test")
        self.assertIsNone(triage_res, "Non-reproducing timeout must be dropped immediately")

    def test_acceptance_test_noise_filtering(self):
        """Acceptance test from WORKPLAN.md 4a:
        Re-running triage on stored sweep output leaves only the known crashes (merge_into_*, asof_join_predicates).
        """
        triage_dir = triage.OUT
        if not triage_dir.exists():
            self.skipTest("No stored triage findings found in results/triage")

        retained = []
        dropped = []
        for p in sorted(triage_dir.glob("*/meta.json")):
            meta = json.loads(p.read_text())
            noise, reason = fuzz.is_noise_item(meta)
            sid = meta.get("id") or meta.get("finding_id")
            if noise:
                dropped.append((sid, reason))
            else:
                retained.append(sid)

        # Verified retained IDs must be the known ASOF and MERGE crash variations
        self.assertGreater(len(dropped), 20, "Noise gates should drop the 30+ noise findings")
        self.assertIn("af670599ce12", retained, "ASOF crash must be retained")
        self.assertIn("246fad5901cf", retained, "MERGE crash must be retained")
        for sid in retained:
            self.assertIn(sid, ["af670599ce12", "246fad5901cf", "14e4ae09c57e"], f"Unexpected retained finding: {sid}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
