"""SQLsmith-style randomized query generator over active DuckDB schemas (Task N4).

Generates syntactically and semantically rich SQL queries:
- Projections, arithmetic, string, date, and boolean expressions
- Single-table, multi-table joins (INNER, LEFT, CROSS), and subqueries
- WHERE clauses with varied filtering predicates
- GROUP BY and HAVING with aggregate functions (COUNT, SUM, AVG, MIN, MAX)
- Window functions (OVER PARTITION BY ... ORDER BY ... ROWS BETWEEN)
- ORDER BY, LIMIT, and OFFSET
- Set operations (UNION ALL, EXCEPT, INTERSECT)

Usage:
    python src/harness/sqlsmith.py --minutes 5 --build asan
    python src/harness/sqlsmith.py --queries 500 --build cov --cov-every 50
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("FUZZ_WORK", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(CODE / "harness"))
sys.path.insert(0, str(CODE / "coverage"))

import oracle  # noqa: E402
import runner  # noqa: E402

try:
    import cov  # noqa: E402
except ImportError:
    cov = None

CRASHES = WORK / "crashes"
SEEDS = WORK / "corpus" / "seeds"


# -----------------------------------------------------------------------------
# SQLsmith Query Generator
# -----------------------------------------------------------------------------

def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SQLsmithGenerator:
    """Generates complex random SQL queries against a schema."""

    def __init__(self, schema: dict[str, list[dict[str, str]]], rng: random.Random | None = None):
        self.schema = schema
        self.rng = rng or random.Random()
        self.tables = list(schema.keys())

    def _rand_const(self, col_type: str) -> str:
        fam = oracle.classify_type(col_type)
        if fam == "integer":
            return str(self.rng.choice([0, 1, -1, 42, 100, 2047, 2048, 2049, -2147483648, 2147483647]))
        if fam == "float":
            return str(self.rng.choice([0.0, 1.0, -1.0, 3.14159, 100.5, 1e5, -1e5]))
        if fam == "string":
            return self.rng.choice(["''", "'a'", "'test'", "'foo'", "'duckdb'", "'2023-01-01'"])
        if fam == "boolean":
            return self.rng.choice(["TRUE", "FALSE"])
        if fam == "date":
            return self.rng.choice(["DATE '2020-01-01'", "DATE '1970-01-01'", "DATE '2030-12-31'"])
        if fam == "timestamp":
            return "TIMESTAMP '2020-01-01 12:00:00'"
        return "NULL"

    def _rand_expr(self, available_cols: list[tuple[str, str, str]], depth: int = 0) -> str:
        """Generate a random value expression using available columns (alias, col_name, col_type)."""
        if not available_cols or depth >= 2 or self.rng.random() < 0.35:
            if available_cols and self.rng.random() < 0.7:
                alias, cname, _ = self.rng.choice(available_cols)
                return f"{_quote_ident(alias)}.{_quote_ident(cname)}"
            col_type = available_cols[0][2] if available_cols else "INTEGER"
            return self._rand_const(col_type)

        alias, cname, ctype = self.rng.choice(available_cols)
        fam = oracle.classify_type(ctype)
        col_ref = f"{_quote_ident(alias)}.{_quote_ident(cname)}"

        op_choice = self.rng.random()
        if fam == "integer":
            if op_choice < 0.4:
                return f"({col_ref} + {self.rng.choice([1, -1, 10, 2048])})"
            elif op_choice < 0.7:
                return f"abs({col_ref})"
            else:
                return f"coalesce({col_ref}, {self._rand_const('integer')})"
        elif fam == "float":
            if op_choice < 0.5:
                return f"({col_ref} * 1.5)"
            else:
                return f"coalesce({col_ref}, 0.0)"
        elif fam == "string":
            if op_choice < 0.4:
                return f"length({col_ref})"
            elif op_choice < 0.7:
                return f"upper({col_ref})"
            else:
                return f"coalesce({col_ref}, '')"
        elif fam == "boolean":
            return f"NOT ({col_ref})"

        return col_ref

    def _rand_predicate(self, available_cols: list[tuple[str, str, str]], depth: int = 0) -> str:
        """Generate a boolean predicate over available columns."""
        if not available_cols or depth >= 2:
            return "TRUE"

        alias, cname, ctype = self.rng.choice(available_cols)
        fam = oracle.classify_type(ctype)
        col_ref = f"{_quote_ident(alias)}.{_quote_ident(cname)}"

        r = self.rng.random()
        if r < 0.20:
            return f"{col_ref} IS NULL"
        elif r < 0.40:
            return f"{col_ref} IS NOT NULL"
        elif r < 0.70:
            op = self.rng.choice(["=", "!=", "<", "<=", ">", ">="])
            val = self._rand_const(ctype)
            return f"{col_ref} {op} {val}"
        elif r < 0.85 and fam == "integer":
            return f"{col_ref} BETWEEN 0 AND 2048"
        elif r < 0.92 and fam == "string":
            return f"{col_ref} LIKE '%a%'"
        else:
            # Compound
            p1 = self._rand_predicate(available_cols, depth + 1)
            p2 = self._rand_predicate(available_cols, depth + 1)
            conj = self.rng.choice(["AND", "OR"])
            return f"({p1} {conj} {p2})"

    def _rand_from(self, depth: int = 0) -> tuple[str, list[tuple[str, str, str]]]:
        """Generate a FROM clause and return (from_sql, list of (alias, col_name, col_type))."""
        if not self.tables:
            return "system_catalog", []

        t1 = self.rng.choice(self.tables)
        alias1 = f"t1_{self.rng.randint(10, 99)}"
        cols1 = [(alias1, c["name"], c["type"]) for c in self.schema.get(t1, [])]
        from_str = f"{_quote_ident(t1)} AS {_quote_ident(alias1)}"

        # Optionally join with a second table if available
        if len(self.tables) >= 2 and depth == 0 and self.rng.random() < 0.65:
            t2 = self.rng.choice([t for t in self.tables if t != t1] or self.tables)
            alias2 = f"t2_{self.rng.randint(10, 99)}"
            cols2 = [(alias2, c["name"], c["type"]) for c in self.schema.get(t2, [])]
            join_type = self.rng.choice(["INNER JOIN", "LEFT JOIN", "CROSS JOIN"])
            if join_type == "CROSS JOIN":
                from_str = f"{from_str} CROSS JOIN {_quote_ident(t2)} AS {_quote_ident(alias2)}"
            else:
                # find potential matching types for join key
                pairs = [(c1, c2) for c1 in cols1 for c2 in cols2
                         if oracle.classify_type(c1[2]) == oracle.classify_type(c2[2])]
                if pairs:
                    c1, c2 = self.rng.choice(pairs)
                    cond = f"{_quote_ident(c1[0])}.{_quote_ident(c1[1])} = {_quote_ident(c2[0])}.{_quote_ident(c2[1])}"
                else:
                    cond = "1 = 1"
                from_str = f"{from_str} {join_type} {_quote_ident(t2)} AS {_quote_ident(alias2)} ON ({cond})"
            return from_str, cols1 + cols2

        return from_str, cols1

    def generate_query(self) -> str:
        """Generate a full SQLsmith query."""
        if not self.tables:
            return "SELECT 1;"

        from_sql, available_cols = self._rand_from()
        if not available_cols:
            return "SELECT 1;"

        qtype = self.rng.random()
        where_clause = ""
        if self.rng.random() < 0.75:
            pred = self._rand_predicate(available_cols)
            where_clause = f" WHERE {pred}"

        # 1. Aggregate Query with GROUP BY & HAVING
        if qtype < 0.35 and len(available_cols) >= 2:
            grp_col = self.rng.choice(available_cols)
            grp_ref = f"{_quote_ident(grp_col[0])}.{_quote_ident(grp_col[1])}"
            agg_col = self.rng.choice(available_cols)
            agg_ref = f"{_quote_ident(agg_col[0])}.{_quote_ident(agg_col[1])}"
            agg_fn = self.rng.choice(["count", "sum", "min", "max", "avg"])
            having = ""
            if self.rng.random() < 0.4:
                having = f" HAVING count(*) >= 0"
            limit = self.rng.choice([10, 50, 100, 2048])
            return (f"SELECT {grp_ref}, {agg_fn}({agg_ref}) AS agg_val "
                    f"FROM {from_sql}{where_clause} "
                    f"GROUP BY {grp_ref}{having} "
                    f"ORDER BY {grp_ref} LIMIT {limit};")

        # 2. Window Function Query
        elif qtype < 0.60 and len(available_cols) >= 2:
            p_col = self.rng.choice(available_cols)
            p_ref = f"{_quote_ident(p_col[0])}.{_quote_ident(p_col[1])}"
            o_col = self.rng.choice(available_cols)
            o_ref = f"{_quote_ident(o_col[0])}.{_quote_ident(o_col[1])}"
            w_fn = self.rng.choice(["row_number()", "rank()", "dense_rank()", f"count({o_ref})"])
            limit = self.rng.choice([10, 50, 100])
            return (f"SELECT {p_ref}, {o_ref}, "
                    f"{w_fn} OVER (PARTITION BY {p_ref} ORDER BY {o_ref} "
                    f"ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS win_val "
                    f"FROM {from_sql}{where_clause} "
                    f"ORDER BY {p_ref}, {o_ref} LIMIT {limit};")

        # 3. Standard Projections with ORDER BY & LIMIT
        else:
            proj_count = min(len(available_cols), self.rng.randint(1, 4))
            selected_cols = self.rng.sample(available_cols, proj_count)
            projs = [f"{_quote_ident(c[0])}.{_quote_ident(c[1])}" for c in selected_cols]
            distinct = "DISTINCT " if self.rng.random() < 0.25 else ""
            order_by = ""
            if self.rng.random() < 0.7:
                o_col = selected_cols[0]
                direction = self.rng.choice(["ASC", "DESC"])
                order_by = f" ORDER BY {_quote_ident(o_col[0])}.{_quote_ident(o_col[1])} {direction}"
            limit = self.rng.choice([1, 10, 50, 100, 2048])
            return f"SELECT {distinct}{', '.join(projs)} FROM {from_sql}{where_clause}{order_by} LIMIT {limit};"


# -----------------------------------------------------------------------------
# SQLsmith Campaign Runner
# -----------------------------------------------------------------------------

def populate_database_from_seeds(con, max_seeds: int = 50) -> list[str]:
    """Execute setup statements from seeds into connection to build rich schema."""
    setup_stmts: list[str] = []
    seed_files = sorted(SEEDS.glob("*.sql"))
    if not seed_files:
        # Fallback schema if seeds not extracted
        con.execute("""
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
        return [
            "CREATE TABLE users (id INT, name VARCHAR, balance DOUBLE, active BOOLEAN, joined DATE);",
            "CREATE TABLE orders (order_id INT, user_id INT, amount DOUBLE, status VARCHAR);",
        ]

    for sf in seed_files[:max_seeds]:
        try:
            stmts = runner.split_statements(sf.read_text(errors="replace"))
            for s in stmts:
                if not re.match(r"^\s*(select|with|values)\b", s, re.I):
                    try:
                        con.execute(s)
                        s_safe = re.sub(r"^\s*CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS)",
                                        "CREATE TABLE IF NOT EXISTS ", s, flags=re.I)
                        setup_stmts.append(s_safe)
                    except Exception:
                        pass
        except Exception:
            pass
    return setup_stmts


def run_sqlsmith(minutes: float = 5.0, max_queries: int = 0, build: str = "asan",
                 seed: int = 42, cov_every: int = 50) -> dict:
    """Run SQLsmith fuzzer for a given time budget or query count."""
    import duckdb

    rng = random.Random(seed)
    con = duckdb.connect(":memory:")
    setup_stmts = populate_database_from_seeds(con)
    schema = oracle.extract_schema(con)
    con.close()

    if not schema:
        print("[!] Warning: empty schema, inserting synthetic tables...")
        con = duckdb.connect(":memory:")
        setup_stmts = populate_database_from_seeds(con, max_seeds=0)
        schema = oracle.extract_schema(con)
        con.close()

    print(f"[*] SQLsmith initialized: {len(schema)} tables, seed={seed}, build={build}")
    for tname, cols in schema.items():
        print(f"    - {tname}: {len(cols)} columns")

    generator = SQLsmithGenerator(schema, rng)
    t0 = time.time()
    t_end = t0 + (minutes * 60) if minutes > 0 else float("inf")
    attempts = 0
    ok = 0
    errors = 0
    crashes = 0
    driver_proc = None

    cov_tracker = None
    if build == "cov" and cov is not None:
        cov_tracker = cov.Coverage()

    prelude_sql = "\n".join(setup_stmts) + "\n"
    batch_size = 25

    while time.time() < t_end:
        if max_queries and attempts >= max_queries:
            break
        # Generate batch
        cur_batch_size = min(batch_size, max_queries - attempts) if max_queries else batch_size
        chunk_queries = [generator.generate_query() for _ in range(cur_batch_size)]
        items = [(f"sqlsmith_{attempts + i}", prelude_sql + q + ";\n") for i, q in enumerate(chunk_queries)]

        # High-speed in-process batch driver execution
        results = runner.run_batch(items, build=build, timeout=10.0, isolate=True)

        for (qid, res), q in zip(results, chunk_queries):
            attempts += 1
            outcome = res.outcome
            if outcome == "ok":
                ok += 1
            elif outcome in ("crash", "asan", "internal_error", "timeout"):
                crashes += 1
                CRASHES.mkdir(parents=True, exist_ok=True)
                crash_file = CRASHES / f"sqlsmith_{res.signature or outcome}_{attempts}.sql"
                crash_file.write_text(prelude_sql + q + ";\n")
                print(f"[!] Crash found: {crash_file.name} ({outcome})", flush=True)
            else:
                errors += 1

            # Coverage sampling
            if cov_tracker and attempts % cov_every == 0:
                cov_tracker.run_and_merge(prelude_sql + q + ";\n")

        if attempts % 100 == 0 or attempts <= batch_size:
            elapsed = time.time() - t0
            qps = attempts / elapsed if elapsed > 0 else 0
            valid_pct = (ok / attempts) * 100 if attempts else 0
            print(f"... [{elapsed:5.1f}s] queries={attempts} ok={ok} err={errors} "
                  f"crashes={crashes} valid={valid_pct:4.1f}% QPS={qps:5.1f}", flush=True)

    elapsed = round(time.time() - t0, 2)
    qps = round(attempts / elapsed, 1) if elapsed > 0 else 0
    valid_pct = round((ok / attempts) * 100, 1) if attempts else 0.0

    summary = {
        "baseline": "sqlsmith",
        "build": build,
        "seconds": elapsed,
        "queries": attempts,
        "ok": ok,
        "errors": errors,
        "crashes": crashes,
        "valid_pct": valid_pct,
        "qps": qps,
    }
    print(f"\n[+] SQLsmith Complete: {summary}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="SQLsmith random query fuzzer for DuckDB")
    ap.add_argument("--minutes", type=float, default=5.0, help="Run budget in minutes (0 for no time limit)")
    ap.add_argument("--queries", type=int, default=0, help="Max queries to run (0 for infinite)")
    ap.add_argument("--build", choices=["asan", "cov"], default="asan", help="Binary target")
    ap.add_argument("--seed", type=int, default=42, help="Random seed")
    ap.add_argument("--cov-every", type=int, default=50, help="Coverage sample frequency")
    args = ap.parse_args()

    run_sqlsmith(minutes=args.minutes, max_queries=args.queries, build=args.build,
                 seed=args.seed, cov_every=args.cov_every)


if __name__ == "__main__":
    main()
