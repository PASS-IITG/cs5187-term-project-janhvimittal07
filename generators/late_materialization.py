"""Generator targeting DuckDB 2.0.0's late_materialization optimizer.

Stresses delaying materialization of wide varchar payloads past selective filters,
joins, order by clauses, and limits.
"""
import random

META = {
    "name": "late_materialization",
    "target": {"optimizer": "late_materialization"},
    "description": "late materialization of wide payloads across selective filters, joins, and limits",
    "author_model": "Gemini 2.5 Pro (Antigravity)",
}


def generate(rng):
    n = rng.choice([500, 1000, 2000])
    setup = [
        "SET profiling_renderer_settings = MAP {'operator_casing': 'upper'}",
        "DROP TABLE IF EXISTS ref_tbl",
        "DROP TABLE IF EXISTS wide_tbl",
        "CREATE TABLE wide_tbl(id INT, v INT, grp INT, payload VARCHAR, note VARCHAR)",
        f"INSERT INTO wide_tbl SELECT i, i % 100, i % 5, repeat('x', 120), ('note_' || (i % 10)) FROM range({n}) r(i)",
        "CREATE TABLE ref_tbl(id INT, label VARCHAR, code INT)",
        "INSERT INTO ref_tbl SELECT i, ('ref_' || i), i % 3 FROM range(50) r(i)",
        "ANALYZE",
    ]

    qs = []
    num_queries = rng.randint(2, 3)
    for _ in range(num_queries):
        lim = rng.randint(2, 25)
        thresh = rng.randint(10, 80)
        filter_col = rng.choice(["id", "v", "grp"])
        ord_col = rng.choice(["id", "v", "grp"])
        direction = rng.choice(["ASC", "DESC"])

        op = rng.choice([">", ">=", "<", "<="])
        use_join = rng.random() < 0.35
        if use_join:
            jtype = rng.choice(["JOIN", "LEFT JOIN"])
            core = (
                f"SELECT w.id, w.payload, r.label FROM wide_tbl w "
                f"{jtype} ref_tbl r USING (id) "
                f"WHERE w.{filter_col} {op} {thresh} "
                f"ORDER BY w.{ord_col} {direction} "
                f"LIMIT {lim}"
            )
        else:
            sel_cols = rng.choice(["id, payload", "payload, id", "id, payload, note", "payload"])
            core = (
                f"SELECT {sel_cols} FROM wide_tbl "
                f"WHERE {filter_col} {op} {thresh} "
                f"ORDER BY {ord_col} {direction} "
                f"LIMIT {lim}"
            )

        form = rng.randint(0, 11)
        if form == 0:
            qs.append(f"{core}")
        elif form == 1:
            qs.append(f"WITH q AS ({core}) SELECT * FROM q ORDER BY ALL")
        elif form == 2:
            qs.append(f"SELECT count(*), max(1) FROM ({core}) sub")
        elif form == 3:
            qs.append(f"SELECT * FROM ({core}) s WHERE 1=1 ORDER BY ALL")
        elif form == 4:
            qs.append(f"SELECT s.*, row_number() OVER (ORDER BY 1) FROM ({core}) s ORDER BY ALL")
        elif form == 5:
            qs.append(f"SELECT * FROM ({core}) s1 UNION ALL SELECT * FROM ({core}) s2 ORDER BY ALL")
        elif form == 6:
            qs.append(f"SELECT DISTINCT * FROM ({core}) sub ORDER BY ALL")
        elif form == 7:
            qs.append(f"SELECT substring(payload, 1, 10) FROM ({core}) sub")
        elif form == 8:
            qs.append(f"WITH cte AS ({core}) SELECT count(*) FROM cte")
        elif form == 9:
            qs.append(f"SELECT 1, count(*) FROM ({core}) s GROUP BY 1")
        elif form == 10:
            qs.append(f"WITH q AS ({core}) SELECT q.*, r.code FROM q JOIN ref_tbl r ON 1=1 WHERE r.id = 1")
        else:
            qs.append(f"SELECT * FROM ({core}) s LIMIT 2")

    return {"setup": setup, "queries": qs}
