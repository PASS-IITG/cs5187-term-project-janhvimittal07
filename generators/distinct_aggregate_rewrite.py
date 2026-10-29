"""Generator targeting DuckDB 2.0.0's distinct_aggregate_rewrite optimizer.

Tests logical rewrites of multiple DISTINCT aggregates and mixed distinct/non-distinct aggregates
into streaming fanout CTEs and execution plans across diverse table shapes and groupings.
"""
import random

META = {
    "name": "distinct_aggregate_rewrite",
    "target": {"optimizer": "distinct_aggregate_rewrite"},
    "description": "distinct_aggregate_rewrite with multiple distinct inputs, mixed aggs, diverse groupings and wrappers",
    "author_model": "Gemini 2.5 Pro (Antigravity)",
}

COLS = ["a", "b", "c", "d"]
DIST_AGGS = [
    "count(DISTINCT a)",
    "count(DISTINCT b)",
    "count(DISTINCT c)",
    "sum(DISTINCT d)",
    "avg(DISTINCT e)",
    "min(DISTINCT a)",
    "max(DISTINCT b)",
    "sum(DISTINCT e)",
]
REG_AGGS = ["count(*)", "sum(d)", "avg(e)", "min(a)", "max(b)"]


def generate(rng):
    n = rng.choice([20, 50, 150, 300])
    setup = [
        "SET profiling_renderer_settings = MAP {'operator_casing': 'upper'}",
        "DROP TABLE IF EXISTS t",
        "CREATE TABLE t(a INTEGER, b INTEGER, c VARCHAR, d INTEGER, e DOUBLE)",
        f"INSERT INTO t SELECT i % {rng.randint(2, 8)}, CASE WHEN i % 4 = 0 THEN NULL ELSE i % 5 END, (i % 6)::VARCHAR, (i % 11) * 2, i * 0.5 FROM range({n}) r(i)",
    ]

    qs = []
    num_queries = rng.randint(6, 12)
    for _ in range(num_queries):
        num_dist = rng.randint(1, 3)
        num_reg = rng.randint(0, 2)
        aggs = ", ".join(rng.sample(DIST_AGGS, num_dist) + rng.sample(REG_AGGS, num_reg))

        grp_cols = rng.sample(COLS, rng.randint(0, 2))
        grp = (" GROUP BY " + ", ".join(grp_cols)) if grp_cols else ""
        sel = (", ".join(grp_cols) + ", " + aggs) if grp_cols else aggs

        where_clause = ""
        if rng.random() < 0.4:
            col = rng.choice(["a", "b", "d"])
            op = rng.choice([">", "<", "=", "!="])
            val = rng.randint(0, 5)
            where_clause = f" WHERE {col} {op} {val}"

        having_clause = ""
        if grp and rng.random() < 0.3:
            having_clause = f" HAVING count(*) > {rng.randint(0, 3)}"

        core = f"SELECT {sel} FROM t{where_clause}{grp}{having_clause}"

        wrapper = rng.randint(0, 5)
        if wrapper == 0:
            qs.append(f"{core} ORDER BY ALL")
        elif wrapper == 1:
            qs.append(f"WITH q AS ({core}) SELECT * FROM q ORDER BY ALL")
        elif wrapper == 2:
            qs.append(f"SELECT count(*), max(m) FROM (SELECT {sel}, count(*) AS m FROM t{where_clause}{grp}) s")
        elif wrapper == 3:
            qs.append(f"SELECT * FROM ({core}) s WHERE 1=1 ORDER BY ALL LIMIT {rng.randint(5, 50)}")
        elif wrapper == 4:
            qs.append(f"SELECT s.*, row_number() OVER () FROM ({core}) s ORDER BY ALL")
        else:
            qs.append(f"{core}")

    return {"setup": setup, "queries": qs}
