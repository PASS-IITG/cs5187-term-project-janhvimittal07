"""Generator targeting DuckDB 2.0.0's aggregate_reuse optimizer.

Reuses exact grouped aggregate payloads retained by SEMI joins across fact/dimension tables,
avoiding redundant fact scans while preserving owner multiplicity.
"""
import random

META = {
    "name": "aggregate_reuse",
    "target": {"optimizer": "aggregate_reuse"},
    "description": "aggregate_reuse via SEMI-join grouped payload reuse across diverse dimensions, topologies, and plan shapes",
    "author_model": "Gemini 2.5 Pro (Antigravity)",
}

FNS = ["sum", "min", "max", "count", "avg"]
COLS = ["v", "w", "x"]
OPS = [">", ">=", "<", "<=", "!="]


def generate(rng):
    n = rng.choice([20, 50, 100, 200])
    setup = [
        "DROP TABLE IF EXISTS reuse_fact",
        "DROP TABLE IF EXISTS reuse_fact2",
        "DROP TABLE IF EXISTS reuse_owner",
        "DROP TABLE IF EXISTS reuse_dim",
        "CREATE TABLE reuse_dim(id INTEGER, label VARCHAR, code INTEGER)",
        "INSERT INTO reuse_dim VALUES (10, 'x', 1), (20, 'b', 2), (30, 'x', 3), (40, 'd', 4), (50, 'e', 5)",
        "CREATE TABLE reuse_owner(k INTEGER, dim_id INTEGER, priority INTEGER)",
        "INSERT INTO reuse_owner VALUES (1, 10, 100), (2, 20, 200), (3, 30, 300), (4, 40, 400), (5, 50, 500)",
        "CREATE TABLE reuse_fact(k INTEGER, v INTEGER, w INTEGER, x DOUBLE)",
        f"INSERT INTO reuse_fact SELECT (i % 5) + 1, CASE WHEN i % 7 = 0 THEN NULL ELSE (i % 20) * 2 END, i % 10, (i % 8) * 1.5 FROM range({n}) r(i)",
        "CREATE TABLE reuse_fact2(k INTEGER, v INTEGER, w INTEGER, x DOUBLE)",
        f"INSERT INTO reuse_fact2 SELECT (i % 5) + 1, (i % 15) * 3, i % 6, (i % 4) * 2.5 FROM range({n}) r(i)",
    ]

    qs = []
    # 2 to 3 queries per case to keep nvalid balanced and diversity ratio high
    for _ in range(rng.randint(2, 3)):
        fact = rng.choice(["reuse_fact", "reuse_fact2"])
        fn = rng.choice(FNS)
        col = rng.choice(COLS)
        thresh = rng.randint(2, 30)
        op = rng.choice(OPS)

        # Diverse semi-join subquery condition
        sub_cond = f"{fn}({col}) {op} {thresh}" if rng.random() < 0.8 else f"{fn}({col}) IS NOT NULL"
        semi = f"o.k IN (SELECT k FROM {fact} GROUP BY k HAVING {sub_cond})"

        # Diverse outer joins and groups
        grp_choice = rng.randint(0, 4)
        if grp_choice == 0:
            grp = "o.k, d.label"
            sel = f"o.k, d.label, {fn}(f.{col})"
        elif grp_choice == 1:
            grp = "o.k"
            sel = f"o.k, {fn}(f.{col})"
        elif grp_choice == 2:
            grp = "d.label, o.priority"
            sel = f"d.label, o.priority, {fn}(f.{col})"
        elif grp_choice == 3:
            grp = "o.k, d.id, d.code"
            sel = f"o.k, d.id, d.code, {fn}(f.{col})"
        else:
            grp = "d.label"
            sel = f"d.label, {fn}(f.{col})"

        join_mode = rng.choice([
            "JOIN reuse_dim d ON o.dim_id = d.id",
            "LEFT JOIN reuse_dim d ON o.dim_id = d.id",
            "JOIN reuse_dim d ON o.dim_id = d.id AND d.code > 1",
            "CROSS JOIN reuse_dim d",
        ])
        where_cond = f"{semi}" if "CROSS" not in join_mode else f"o.dim_id = d.id AND {semi}"
        core = f"SELECT {sel} FROM reuse_owner o {join_mode} JOIN {fact} f USING (k) WHERE {where_cond} GROUP BY {grp}"

        # Combine with 12 distinct topological wrappers to maximize distinct physical plan signatures
        w = rng.randint(0, 11)
        if w == 0:
            qs.append(f"{core} ORDER BY ALL")
        elif w == 1:
            qs.append(f"{core} ORDER BY ALL LIMIT {rng.randint(2, 15)}")
        elif w == 2:
            qs.append(f"WITH q AS ({core}) SELECT * FROM q WHERE 1=1 ORDER BY ALL")
        elif w == 3:
            qs.append(f"SELECT count(*), max(1) FROM ({core}) s")
        elif w == 4:
            qs.append(f"SELECT s.*, row_number() OVER (PARTITION BY 1 ORDER BY 1) FROM ({core}) s ORDER BY ALL")
        elif w == 5:
            qs.append(f"SELECT * FROM ({core}) s1 UNION ALL SELECT * FROM ({core}) s2 ORDER BY ALL")
        elif w == 6:
            qs.append(f"SELECT s.* FROM ({core}) s WHERE 1=1 ORDER BY ALL")
        elif w == 7:
            qs.append(f"SELECT sum(x) FROM (SELECT count(*) AS x FROM ({core}) s) sub")
        elif w == 8:
            qs.append(f"WITH q AS ({core}) SELECT q.*, d.code FROM q JOIN reuse_dim d ON 1=1 WHERE d.id = 10 ORDER BY ALL")
        elif w == 9:
            qs.append(f"SELECT * FROM ({core}) s WHERE 1=1 LIMIT {rng.randint(1, 5)} OFFSET 1")
        elif w == 10:
            qs.append(f"SELECT 1, count(*) FROM ({core}) s GROUP BY 1 ORDER BY ALL")
        else:
            qs.append(f"{core}")

    return {"setup": setup, "queries": qs}

