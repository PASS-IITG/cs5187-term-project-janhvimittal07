#!/usr/bin/env python3
"""Automated tests and benchmarks for the DuckDB in-process batch driver.

Verifies:
1. Normal query execution and return codes
2. Error classification (parse_error, binder_error, runtime_error)
3. Timeout interruption via watchdog thread
4. Catalog isolation between queries (no schema leakage)
5. Crash isolation on real upstream reproducers (ASOF null-pointer crash & MERGE rowid assert)
6. QPS throughput benchmark comparing in-process driver against standard CLI
"""
import json
import os
import subprocess
import time
import unittest
from pathlib import Path

DRIVER_DIR = Path(__file__).resolve().parent
DRIVER_BIN = DRIVER_DIR / "driver-asan"
CLI_BIN = Path(os.environ.get("FUZZ_WORK", Path.home() / "fuzzwork")) / "build-asan" / "duckdb"
REPRO_DIR = Path(os.environ.get("FUZZ_WORK", Path.home() / "fuzzwork")) / "reproducers"


class TestDriver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DRIVER_BIN.exists():
            subprocess.run(["make", "-C", str(DRIVER_DIR), "asan"], check=True)

    def run_driver(self, tasks: list[dict], extra_args: list[str] | None = None) -> list[dict]:
        cmd = [str(DRIVER_BIN)] + (extra_args or [])
        input_data = "\n".join(json.dumps(t) for t in tasks) + "\n"
        p = subprocess.run(cmd, input=input_data, text=True, capture_output=True, check=True)
        results = []
        for line in p.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                results.append(json.loads(line))
        return results

    def test_basic_queries(self):
        tasks = [
            {"id": "q_ok", "sql": "SELECT 42 AS ans;", "timeout_ms": 2000},
            {"id": "q_multiline", "sql": "SELECT 1;\nSELECT 2;", "timeout_ms": 2000},
        ]
        results = self.run_driver(tasks)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["outcome"], "ok")
        self.assertEqual(results[0]["returncode"], 0)
        self.assertEqual(results[1]["outcome"], "ok")

    def test_error_classification(self):
        tasks = [
            {"id": "q_parse", "sql": "SELEC 1;", "timeout_ms": 2000},
            {"id": "q_binder", "sql": "SELECT * FROM missing_tbl_xyz;", "timeout_ms": 2000},
            {"id": "q_runtime", "sql": "SELECT cast('abc' as integer);", "timeout_ms": 2000},
        ]
        results = self.run_driver(tasks)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["outcome"], "parse_error")
        self.assertEqual(results[1]["outcome"], "binder_error")
        self.assertEqual(results[2]["outcome"], "runtime_error")

    def test_timeout_interruption(self):
        tasks = [
            {"id": "q_slow", "sql": "SELECT count(*) FROM range(10000000000) t1, range(10000) t2;", "timeout_ms": 200},
            {"id": "q_after_timeout", "sql": "SELECT 99;", "timeout_ms": 2000},
        ]
        t0 = time.time()
        results = self.run_driver(tasks)
        elapsed = time.time() - t0
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["outcome"], "timeout")
        self.assertLess(elapsed, 2.0, "Watchdog failed to interrupt slow query in time")
        self.assertEqual(results[1]["outcome"], "ok")

    def test_catalog_isolation(self):
        tasks = [
            {"id": "q_create", "sql": "CREATE TABLE iso_test(x INT); INSERT INTO iso_test VALUES (1);", "timeout_ms": 2000},
            {"id": "q_check_empty", "sql": "SELECT * FROM iso_test;", "timeout_ms": 2000},
        ]
        results = self.run_driver(tasks)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["outcome"], "ok")
        # In isolated mode, iso_test should not exist in the second query
        self.assertEqual(results[1]["outcome"], "binder_error")

    def test_crash_isolation_and_recovery(self):
        asof_path = REPRO_DIR / "asof_issue_26300.sql"
        merge_path = REPRO_DIR / "merge_pr_26715.sql"
        if not asof_path.exists() or not merge_path.exists():
            self.skipTest("Reproducer files not found in ~/fuzzwork/reproducers")

        tasks = [
            {"id": "q_before", "sql": "SELECT 1;", "timeout_ms": 5000},
            {"id": "q_asof_crash", "sql": asof_path.read_text(), "timeout_ms": 5000},
            {"id": "q_between", "sql": "SELECT 2;", "timeout_ms": 5000},
            {"id": "q_merge_internal", "sql": merge_path.read_text(), "timeout_ms": 5000},
            {"id": "q_after", "sql": "SELECT 3;", "timeout_ms": 5000},
        ]

        results = self.run_driver(tasks)
        self.assertEqual(len(results), 5)
        self.assertEqual(results[0]["outcome"], "ok")
        self.assertEqual(results[1]["outcome"], "asan")
        self.assertIn("UndefinedBehaviorSanitizer", results[1]["stderr"])
        self.assertEqual(results[2]["outcome"], "ok")
        self.assertEqual(results[3]["outcome"], "internal_error")
        self.assertIn("INTERNAL Error", results[3]["stderr"])
        self.assertEqual(results[4]["outcome"], "ok")


def run_benchmark():
    print("\n" + "=" * 60)
    print("RUNNING QPS BENCHMARK: In-Process Driver vs CLI Process")
    print("=" * 60)

    n_queries = 200
    queries = [{"id": f"q_{i}", "sql": f"SELECT {i} + 1 AS col;", "timeout_ms": 2000} for i in range(n_queries)]

    # 1. Benchmark Driver (Isolated)
    t0 = time.time()
    input_data = "\n".join(json.dumps(q) for q in queries) + "\n"
    p = subprocess.run([str(DRIVER_BIN), "--isolate"], input=input_data, text=True, capture_output=True, check=True)
    driver_iso_time = time.time() - t0
    driver_iso_qps = n_queries / driver_iso_time

    # 2. Benchmark Driver (No-Isolate, maximum throughput)
    t0 = time.time()
    p = subprocess.run([str(DRIVER_BIN), "--no-isolate"], input=input_data, text=True, capture_output=True, check=True)
    driver_fast_time = time.time() - t0
    driver_fast_qps = n_queries / driver_fast_time

    # 3. Benchmark CLI (Sample 20 queries to estimate)
    if CLI_BIN.exists():
        cli_n = 20
        t0 = time.time()
        for i in range(cli_n):
            subprocess.run([str(CLI_BIN), ":memory:", "-init", "/dev/null"],
                           input=f"SELECT {i} + 1 AS col;",
                           capture_output=True, text=True, check=True)
        cli_time = time.time() - t0
        cli_qps = cli_n / cli_time
        speedup_iso = driver_iso_qps / cli_qps
        speedup_fast = driver_fast_qps / cli_qps
    else:
        cli_qps = 0.0
        speedup_iso = 0.0
        speedup_fast = 0.0

    print(f"Driver (Isolated catalog):  {n_queries} queries in {driver_iso_time:.3f}s -> {driver_iso_qps:8.1f} QPS")
    print(f"Driver (Shared catalog):    {n_queries} queries in {driver_fast_time:.3f}s -> {driver_fast_qps:8.1f} QPS")
    if cli_qps > 0:
        print(f"Standard CLI Process:        {cli_n} queries in {cli_time:.3f}s -> {cli_qps:8.1f} QPS")
        print(f"Speedup (Isolated vs CLI):  {speedup_iso:8.1f}x")
        print(f"Speedup (Fast vs CLI):      {speedup_fast:8.1f}x")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDriver)
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    if res.wasSuccessful():
        run_benchmark()
    else:
        exit(1)
