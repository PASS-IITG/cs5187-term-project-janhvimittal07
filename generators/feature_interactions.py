"""Generator targeting DuckDB 2.0.0's feature interactions under delim_join_as_cte.

Stresses interactions between correlated subqueries (scalar, EXISTS, IN),
triggers (ROW/STATEMENT), window functions, and complex grouping sets.
"""
import random

META = {
    "name": "feature_interactions",
    "target": {"setting": "delim_join_as_cte"},
    "description": "correlated subquery rewrites interacting with triggers, window functions, and grouping sets",
    "author_model": "Gemini 2.5 Pro (Antigravity)",
}


def generate(rng):
    n = rng.choice([5, 15, 40, 100])
    setup = [
        "SET profiling_renderer_settings = MAP {'operator_casing': 'upper'}",
        "DROP TABLE IF EXISTS audit_log",
        "DROP TABLE IF EXISTS t2",
        "DROP TABLE IF EXISTS t1",
        "CREATE TABLE t1(id INTEGER PRIMARY KEY, k INTEGER, v INTEGER, tag VARCHAR)",
        "CREATE TABLE t2(id INTEGER, k INTEGER, v INTEGER, note VARCHAR)",
        "CREATE TABLE audit_log(id INTEGER, action VARCHAR, v INTEGER)",
        "CREATE TRIGGER trg_t1 AFTER INSERT ON t1 FOR EACH ROW INSERT INTO audit_log VALUES (NEW.id, 'ins', NEW.v)",
        f"INSERT INTO t1 SELECT i, i % 5, (i % 7) * 3, ('tag_' || (i % 4)) FROM range({n}) r(i)",
        f"INSERT INTO t2 SELECT i, i % 5, (i % 9) * 2, ('note_' || (i % 3)) FROM range({n * 2}) r(i)",
    ]

    qs = []
    num_queries = rng.randint(2, 3)
    for _ in range(num_queries):
        sub_type = rng.choice(["scalar", "exists", "not_exists", "in"])
        val_thresh = rng.randint(0, 15)

        if sub_type == "scalar":
            agg_fn = rng.choice(["sum(v)", "avg(v)", "count(*)", "max(v)", "min(v)"])
            subquery = f"(SELECT {agg_fn} FROM t2 WHERE t2.k = t1.k)"
            pred = f"t1.v > {subquery}"
            sel = f"id, k, v, {subquery} AS agg_v"
        elif sub_type == "exists":
            pred = f"EXISTS (SELECT 1 FROM t2 WHERE t2.k = t1.k AND t2.v > {val_thresh})"
            sel = "id, k, v, tag"
        elif sub_type == "not_exists":
            pred = f"NOT EXISTS (SELECT 1 FROM t2 WHERE t2.k = t1.k AND t2.note = 'note_0')"
            sel = "id, k, v, tag"
        else:
            pred = f"t1.v IN (SELECT v FROM t2 WHERE t2.k = t1.k)"
            sel = "id, k, v"

        use_win = rng.random() < 0.35
        win_col = ", row_number() OVER (PARTITION BY k ORDER BY v) AS rn" if use_win else ""

        core = f"SELECT {sel}{win_col} FROM t1 WHERE {pred}"

        form = rng.randint(0, 10)
        if form == 0:
            qs.append(f"{core} ORDER BY ALL")
        elif form == 1:
            qs.append(f"WITH q AS ({core}) SELECT * FROM q WHERE 1=1 ORDER BY ALL")
        elif form == 2:
            qs.append(f"SELECT k, count(*), sum(v) FROM ({core}) sub GROUP BY k ORDER BY ALL")
        elif form == 3:
            qs.append(f"SELECT * FROM ({core}) s WHERE 1=1 ORDER BY ALL LIMIT {rng.randint(3, 20)}")
        elif form == 4:
            qs.append(f"SELECT a.action, count(*), sum(a.v) FROM audit_log a WHERE a.id IN (SELECT id FROM ({core}) s) GROUP BY a.action")
        elif form == 5:
            qs.append(f"SELECT t1.id, t1.k, a.action FROM t1 JOIN audit_log a ON t1.id = a.id WHERE {pred} ORDER BY ALL")
        elif form == 6:
            qs.append(f"SELECT s.*, row_number() OVER (ORDER BY 1) FROM ({core}) s ORDER BY ALL")
        elif form == 7:
            qs.append(f"SELECT * FROM ({core} LIMIT 10) s1 UNION ALL SELECT * FROM ({core} LIMIT 10) s2 ORDER BY ALL")
        elif form == 8:
            qs.append(f"SELECT count(*), max(1) FROM ({core}) s")
        elif form == 9:
            qs.append(f"SELECT 1, count(*) FROM ({core}) s GROUP BY 1 ORDER BY ALL")
        else:
            qs.append(f"SELECT sum(x) FROM (SELECT count(*) AS x FROM ({core}) s) sub")

    return {"setup": setup, "queries": qs}
