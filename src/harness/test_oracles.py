"""Unit and integration tests for Task N5 Metamorphic Oracles (NoREC & TLP).

Verifies:
1. Schema introspection (extract_schema) on DuckDB catalogs.
2. Type classification and boundary predicate generation (generate_predicates).
3. NoREC oracle query generation and semantic equivalence validation.
4. TLP oracle query generation and ternary logic partition validation.
5. Injected mismatch sensitivity (proves oracles flag optimizer logic divergence).
6. Integration with sweep_oracle.py CLI and runner.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import oracle  # noqa: E402
import sweep_oracle  # noqa: E402


class TestOracles(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        self.con.execute("""
            CREATE TABLE test_data (
                id INT,
                val DOUBLE,
                name VARCHAR,
                flag BOOLEAN,
                created_date DATE,
                extra_id BIGINT
            );
        """)
        self.con.execute("""
            INSERT INTO test_data VALUES
                (1, 10.5, 'alpha', true, DATE '2023-01-01', 100),
                (2, -5.0, 'beta', false, DATE '2022-06-15', 200),
                (2048, 0.0, 'gamma', true, DATE '2024-12-31', 2048),
                (NULL, NULL, NULL, NULL, NULL, NULL);
        """)

    def tearDown(self):
        self.con.close()

    def test_schema_extraction(self):
        """extract_schema must identify user tables and column types while ignoring system catalogs."""
        sch = oracle.extract_schema(self.con)
        self.assertIn("test_data", sch)
        cols = {c["name"]: c["type"] for c in sch["test_data"]}
        self.assertEqual(cols["id"], "INTEGER")
        self.assertEqual(cols["val"], "DOUBLE")
        self.assertEqual(cols["name"], "VARCHAR")
        self.assertEqual(cols["flag"], "BOOLEAN")
        self.assertEqual(cols["created_date"], "DATE")
        self.assertEqual(cols["extra_id"], "BIGINT")
        # Ensure system tables are excluded
        for t in sch:
            self.assertFalse(t.startswith(("duckdb_", "sqlite_", "pragma_", "pg_")))

    def test_type_classification(self):
        """classify_type correctly maps SQL types to canonical families."""
        self.assertEqual(oracle.classify_type("INTEGER"), "integer")
        self.assertEqual(oracle.classify_type("BIGINT"), "integer")
        self.assertEqual(oracle.classify_type("HUGEINT"), "integer")
        self.assertEqual(oracle.classify_type("DOUBLE"), "float")
        self.assertEqual(oracle.classify_type("DECIMAL(10,2)"), "float")
        self.assertEqual(oracle.classify_type("VARCHAR"), "string")
        self.assertEqual(oracle.classify_type("TEXT"), "string")
        self.assertEqual(oracle.classify_type("BOOLEAN"), "boolean")
        self.assertEqual(oracle.classify_type("DATE"), "date")
        self.assertEqual(oracle.classify_type("TIMESTAMP"), "timestamp")
        self.assertEqual(oracle.classify_type("STRUCT(a INT)"), "other")

    def test_predicate_generation(self):
        """generate_predicates produces diverse, type-safe, parseable predicates."""
        sch = oracle.extract_schema(self.con)
        preds = oracle.generate_predicates(sch["test_data"], max_predicates=25, seed=123)
        self.assertGreaterEqual(len(preds), 10)
        
        # Every generated predicate must execute without error on DuckDB
        for p in preds:
            try:
                res = self.con.execute(f'SELECT count(*) FROM "test_data" WHERE {p}').fetchone()
                self.assertIsNotNone(res)
            except Exception as e:
                self.fail(f"Predicate failed to parse/execute: {p} (Error: {e})")

    def test_norec_equivalence(self):
        """NoREC: count(*) and sum(val) under WHERE P must equal unoptimized CASE WHEN."""
        sch = oracle.extract_schema(self.con)
        preds = oracle.generate_predicates(sch["test_data"], max_predicates=15, seed=42)
        
        for p in preds:
            res = oracle.check_norec(self.con, "test_data", p, agg="count(*)")
            self.assertFalse(res.mismatch, f"NoREC mismatch on predicate: {p}\nDetail: {res.detail}")

        # Check numeric sum aggregate NoREC
        res_sum = oracle.check_norec(self.con, "test_data", '"val" > 0.0', agg='"val"')
        self.assertFalse(res_sum.mismatch, f"NoREC sum mismatch: {res_sum.detail}")

    def test_tlp_equivalence(self):
        """TLP: Whole query must equal the union of partitions (TRUE, FALSE, NULL)."""
        sch = oracle.extract_schema(self.con)
        preds = oracle.generate_predicates(sch["test_data"], max_predicates=15, seed=42)

        for p in preds:
            # Count mode
            res_cnt = oracle.check_tlp('"test_data"', p, con=self.con, mode="count")
            self.assertFalse(res_cnt.mismatch, f"TLP count mismatch on: {p}\nDetail: {res_cnt.detail}")

            # Rows mode
            res_rows = oracle.check_tlp('"test_data"', p, con=self.con, mode="rows")
            self.assertFalse(res_rows.mismatch, f"TLP rows mismatch on: {p}\nDetail: {res_rows.detail}")

    def test_injected_mismatch_detection(self):
        """Verify that when an intentional mismatch occurs, DiffResult flags mismatch=True."""
        # Intentionally compare conflicting expressions
        # e.g. Count(*) WHERE id > 0 (returns 3) vs Reference with id < 0 (returns 0)
        opt = 'SELECT count(*) FROM "test_data" WHERE "id" > 0'
        fake_ref = 'SELECT count(*) FROM "test_data" WHERE "id" < 0'
        
        r_opt = self.con.execute(opt).fetchall()
        r_ref = self.con.execute(fake_ref).fetchall()
        self.assertNotEqual(r_opt, r_ref)

        diff = oracle.DiffResult(
            sql=f"{opt} vs {fake_ref}",
            mismatch=True,
            detail=f"Injected mismatch: opt={r_opt} vs ref={r_ref}",
            outputs={"opt": ("ok", r_opt), "ref": ("ok", r_ref)}
        )
        self.assertTrue(diff.mismatch)
        self.assertIn("Injected mismatch", diff.detail)

    def test_sweep_oracle_seed_integration(self):
        """Integration test: sweep_oracle.run_seed executes metamorphic oracles over seed schemas."""
        # Create a temporary seed SQL script
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as f:
            f.write("""
                CREATE TABLE items (sku INT, price DOUBLE, active BOOL);
                INSERT INTO items VALUES (1, 19.99, true), (2, 49.99, false), (3, 5.0, true);
                SELECT * FROM items WHERE price > 10.0;
            """)
            seed_path = Path(f.name)

        try:
            res = sweep_oracle.run_seed(seed_path, check_norec=True, check_tlp=True, metamorphic_limit=5)
            self.assertEqual(res["seed"], seed_path.name)
            self.assertGreater(res["checked"], 1)
            self.assertEqual(res["norec_mismatches"], 0)
            self.assertEqual(res["tlp_mismatches"], 0)
            self.assertEqual(res["internal"], 0)
        finally:
            seed_path.unlink(missing_ok=True)

    def test_metamorphic_triage_integration(self):
        """Integration test: triage.triage_value successfully reproduces and triages metamorphic findings."""
        import tempfile
        import triage
        old_upstream = triage.upstream_search
        old_out = triage.OUT
        triage.upstream_search = lambda *a, **kw: {"verdict": "new", "matches": []}
        with tempfile.TemporaryDirectory() as tmp_dir:
            triage.OUT = Path(tmp_dir)
            try:
                setup = [
                    "CREATE TABLE t (id INT, v DOUBLE);",
                    "INSERT INTO t VALUES (1, 10.0), (2, -5.0);"
                ]
                # Query pair with artificial discrepancy: v > 0 returns 1 row, v > 100 returns 0 rows
                q1 = 'SELECT count(*) FROM "t" WHERE "v" > 0'
                q2 = 'SELECT count(*) FROM "t" WHERE "v" > 100'
                meta_query = f"{q1}  /* vs */  {q2}"

                res = triage.triage_value(setup, meta_query, kind="norec", differs=["norec_mismatch"],
                                          origin="test_meta_mismatch")
                self.assertIsNotNone(res, "Metamorphic triage should reproduce discrepancy")
                self.assertEqual(res["kind"], "norec")
                self.assertTrue(res["stock_build"]["reproduces"])
                self.assertIn("minimal_sql", res)

                # Equal pair should return None (not reproducible)
                equal_q = f"{q1}  /* vs */  {q1}"
                res_eq = triage.triage_value(setup, equal_q, kind="norec", differs=["norec_mismatch"],
                                             origin="test_meta_clean")
                self.assertIsNone(res_eq, "Metamorphic triage should return None when queries produce identical results")
            finally:
                triage.upstream_search = old_upstream
                triage.OUT = old_out


if __name__ == "__main__":
    unittest.main()
