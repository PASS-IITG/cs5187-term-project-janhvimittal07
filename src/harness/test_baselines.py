"""Unit and integration tests for Task N4 Baselines (SQLsmith, OSS-Fuzz, and Comparative Harness).

Verifies:
1. SQLsmith query generation syntax and semantic validity over DuckDB schemas.
2. Expression, predicate, and join components of SQLsmith generator.
3. Native OSS-Fuzz libFuzzer target compilation and execution.
4. Comparative baseline evaluation harness and LaTeX table generation.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
CODE = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(CODE / "eval"))

import oracle  # noqa: E402
import ossfuzz_runner  # noqa: E402
import run_baselines  # noqa: E402
import sqlsmith  # noqa: E402


class TestBaselines(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        self.con.execute("""
            CREATE TABLE users (id INT, name VARCHAR, balance DOUBLE, active BOOLEAN, joined DATE);
            INSERT INTO users VALUES
                (1, 'alice', 1500.50, true, DATE '2023-01-01'),
                (2, 'bob', -40.0, false, DATE '2022-05-10'),
                (2048, 'admin', 99999.9, true, DATE '2024-12-31');
            CREATE TABLE orders (order_id INT, user_id INT, amount DOUBLE, status VARCHAR);
            INSERT INTO orders VALUES
                (101, 1, 99.99, 'completed'),
                (102, 1, 20.0, 'pending'),
                (103, 2, 5.0, 'failed');
        """)
        self.schema = oracle.extract_schema(self.con)

    def tearDown(self):
        self.con.close()

    def test_sqlsmith_generator_query_syntax(self):
        """SQLsmith must generate syntactically valid queries that execute on DuckDB."""
        gen = sqlsmith.SQLsmithGenerator(self.schema)
        valid = 0
        total = 20
        for _ in range(total):
            q = gen.generate_query()
            self.assertIsInstance(q, str)
            self.assertTrue(q.strip().startswith("SELECT"))
            try:
                self.con.execute(q)
                valid += 1
            except Exception:
                pass
        # High validity rate on well-formed schema
        self.assertGreaterEqual(valid, 12, f"Validity rate too low: {valid}/{total}")

    def test_sqlsmith_components(self):
        """Test expression, predicate, and from clause generator components."""
        gen = sqlsmith.SQLsmithGenerator(self.schema)
        cols = [("u", "id", "INTEGER"), ("u", "name", "VARCHAR"), ("u", "balance", "DOUBLE")]
        
        # Test random constants
        self.assertIn(gen._rand_const("INTEGER"), ["0", "1", "-1", "42", "100", "2047", "2048", "2049", "-2147483648", "2147483647"])
        self.assertIn(gen._rand_const("BOOLEAN"), ["TRUE", "FALSE"])

        # Test random predicate
        pred = gen._rand_predicate(cols)
        self.assertIsInstance(pred, str)
        self.assertGreater(len(pred), 0)

        # Test random from clause
        from_sql, avail = gen._rand_from()
        self.assertIsInstance(from_sql, str)
        self.assertGreater(len(avail), 0)

    def test_ossfuzz_binary_execution(self):
        """Native duckdb_ossfuzz target binary must execute with libFuzzer."""
        bin_path = ossfuzz_runner.compile_ossfuzz_target(build="asan")
        self.assertTrue(bin_path.exists(), f"Target binary missing: {bin_path}")

        # Fast test with isolated single seed
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "seed1.sql").write_text("SELECT 42;")
            res = ossfuzz_runner.run_ossfuzz(seconds=2, build="asan", max_runs=5, corpus_dir=Path(td))
            self.assertEqual(res["baseline"], "ossfuzz")
            self.assertGreaterEqual(res["queries"], 1)

    def test_latex_table_export(self):
        """LaTeX table generator must format valid publication table."""
        sample_summaries = [
            {"baseline": "ossfuzz", "queries": 15000, "qps": 250.0, "valid_pct": 100.0, "crashes": 0, "line_cov_pct": 28.5},
            {"baseline": "sqlsmith", "queries": 12000, "qps": 200.0, "valid_pct": 84.2, "crashes": 0, "line_cov_pct": 32.1},
            {"baseline": "mutation", "queries": 8000, "qps": 133.3, "valid_pct": 91.0, "crashes": 0, "line_cov_pct": 29.8},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".tex", delete=False) as tf:
            tex_path = Path(tf.name)

        try:
            run_baselines.generate_latex_table(sample_summaries, tex_path)
            content = tex_path.read_text()
            self.assertIn("\\begin{table}", content)
            self.assertIn("OSS-Fuzz (libFuzzer)", content)
            self.assertIn("SQLsmith", content)
            self.assertIn("Seed Mutator (J6)", content)
            self.assertIn("\\toprule", content)
            self.assertIn("\\bottomrule", content)
        finally:
            tex_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
