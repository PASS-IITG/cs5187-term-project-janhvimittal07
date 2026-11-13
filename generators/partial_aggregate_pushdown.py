"""Generator targeting DuckDB 2.0.0's partial_aggregate_pushdown optimizer.

Pushes partial aggregates (EXPORT_STATE / combine_aggr) down through multi-fact joins
onto dimensions, avoiding expensive cross-product fanouts.
"""
import random

META = {
    "name": "partial_aggregate_pushdown",
    "target": {"optimizer": "partial_aggregate_pushdown"},
    "description": "partial aggregate pushdown below joins with multi-fact fanouts and diverse query trees",
    "author_model": "Gemini 2.5 Pro (Antigravity)",
}

AGGS1 = ["sum(f.v)", "count(*)", "min(f.v)", "max(f.v)", "avg(f.v)"]
AGGS2 = ["max(f2.v)", "sum(f2.v)", "min(f2.v)", "count(*)", "avg(f2.v)"]


def generate(rng):
    setup = [
        "SET profiling_renderer_settings = MAP {'operator_casing': 'upper'}",
        "DROP TABLE IF EXISTS fact2",
        "DROP TABLE IF EXISTS fact",
        "DROP TABLE IF EXISTS dim",
        "CREATE TABLE dim AS SELECT i AS k, (i % 2)::INTEGER AS grp, ('lbl' || i)::VARCHAR AS label FROM range(5) r(i)",
        "CREATE TABLE fact AS SELECT (i % 5)::INTEGER AS k, i::INTEGER AS v, (i % 2 = 0) AS flag FROM range(120) r(i)",
        "CREATE TABLE fact2 AS SELECT (i % 5)::INTEGER AS k, (i * 2)::INTEGER AS v, (i % 3 = 0) AS flag2 FROM range(120) r(i)",
        "ANALYZE",
    ]

    qs = []
    num_queries = rng.randint(2, 3)
    for _ in range(num_queries):
        j1 = rng.choice(["JOIN", "LEFT JOIN"])
        j2 = rng.choice(["JOIN", "LEFT JOIN"])

        grp = rng.choice(["d.grp", "d.k", "d.grp, d.k", "d.k, d.grp"])
        a1 = rng.choice(AGGS1)
        a2 = rng.choice(AGGS2)
        aggs = f"{a1}, {a2}"

        where_clause = ""
        w_choice = rng.randint(0, 5)
        if w_choice == 1:
            where_clause = f" WHERE f.v > {rng.randint(0, 50)}"
        elif w_choice == 2:
            where_clause = f" WHERE d.grp = {rng.randint(0, 1)}"
        elif w_choice == 3:
            where_clause = f" WHERE f2.v < {rng.randint(50, 200)}"
        elif w_choice == 4:
            where_clause = " WHERE f.flag = true"
        elif w_choice == 5:
            where_clause = f" WHERE f.v BETWEEN 10 AND {rng.randint(60, 100)}"

        having_clause = ""
        h_choice = rng.randint(0, 4)
        if h_choice == 1:
            having_clause = " HAVING count(*) > 0"
        elif h_choice == 2:
            having_clause = f" HAVING sum(f.v) > {rng.randint(10, 100)}"
        elif h_choice == 3:
            having_clause = f" HAVING max(f2.v) > {rng.randint(20, 150)}"

        core = f"SELECT {grp}, {aggs} FROM fact f {j1} dim d ON f.k = d.k {j2} fact2 f2 ON f.k = f2.k{where_clause} GROUP BY {grp}{having_clause}"

        wrapper = rng.randint(0, 10)
        if wrapper == 0:
            qs.append(f"{core} ORDER BY ALL")
        elif wrapper == 1:
            qs.append(f"WITH q AS ({core}) SELECT * FROM q ORDER BY ALL")
        elif wrapper == 2:
            qs.append(f"SELECT count(*), max(1) FROM ({core}) s")
        elif wrapper == 3:
            qs.append(f"SELECT * FROM ({core}) s WHERE 1=1 ORDER BY ALL")
        elif wrapper == 4:
            qs.append(f"WITH cte AS ({core}) SELECT * FROM cte WHERE 1=1 ORDER BY ALL LIMIT {rng.randint(5, 50)}")
        elif wrapper == 5:
            qs.append(f"SELECT s.*, row_number() OVER (ORDER BY 1) FROM ({core}) s ORDER BY ALL")
        elif wrapper == 6:
            qs.append(f"SELECT * FROM ({core}) s1 UNION ALL SELECT * FROM ({core}) s2 ORDER BY ALL")
        elif wrapper == 7:
            qs.append(f"SELECT sum(x) FROM (SELECT count(*) AS x FROM ({core}) s) sub")
        elif wrapper == 8:
            qs.append(f"WITH q AS ({core}) SELECT q.*, d.label FROM q JOIN dim d ON 1=1 WHERE d.k = 1 ORDER BY ALL")
        elif wrapper == 9:
            qs.append(f"SELECT 1, count(*) FROM ({core}) s GROUP BY 1 ORDER BY ALL")
        else:
            qs.append(f"{core} ORDER BY {grp}")

    return {"setup": setup, "queries": qs}
