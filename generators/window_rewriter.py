"""Generator targeting DuckDB 2.0.0's window_rewriter (RowNumberRewriter) optimizer.

Stresses rewriting ROW_NUMBER() OVER () into base table virtual column row_number scans,
with varied outer filtering, wrapping, joins, aggregations, CTEs, and set operations.
"""
import random

META = {
    "name": "window_rewriter",
    "target": {"optimizer": "window_rewriter"},
    "description": "row_number() OVER () rewriting onto table scan with diverse wrappers, filters, joins",
    "author_model": "Gemini 2.5 Pro (Antigravity)",
}

INT_COLS = ["a", "b", "c"]


def generate(rng):
    setup = []
    for i in range(1, 5):
        setup.append("CREATE TABLE t%d(a INTEGER, b INTEGER, c INTEGER, d VARCHAR)" % i)
        n = rng.choice([5, 20, 50, 150])
        setup.append(
            f"INSERT INTO t{i} SELECT i, (i * 3) % 5, (i * 7) % 4, ('str_' || (i % 6)) FROM range({n}) r(i)"
        )

    qs = []
    for _ in range(rng.randint(1, 2)):
        t = "t%d" % rng.randint(1, 4)
        t2 = "t%d" % rng.randint(1, 4)
        base = f"SELECT row_number() OVER () AS rn, a, b, c, d FROM {t}"
        base2 = f"SELECT row_number() OVER () AS rn, a, b, c, d FROM {t2}"
        v = rng.randint(0, 5)
        c = rng.choice(INT_COLS)
        c2 = rng.choice(INT_COLS)

        expr_choice = rng.randint(0, 7)
        if expr_choice == 0:
            sel_expr = f"w.rn, w.{c}"
        elif expr_choice == 1:
            sel_expr = f"w.rn, w.a + w.b AS sum_ab"
        elif expr_choice == 2:
            sel_expr = f"w.rn, w.a * w.c AS prod_ac"
        elif expr_choice == 3:
            sel_expr = f"w.rn, abs(w.a - w.b) AS diff"
        elif expr_choice == 4:
            sel_expr = f"w.rn, w.{c}, w.{c2}"
        elif expr_choice == 5:
            sel_expr = f"w.rn, w.d || '_tag'"
        elif expr_choice == 6:
            sel_expr = f"w.rn, COALESCE(NULLIF(w.{c}, 0), w.rn)"
        else:
            sel_expr = f"w.rn, CASE WHEN w.rn > {v} THEN w.a ELSE w.b END"

        filter_choice = rng.randint(0, 6)
        if filter_choice == 0:
            where_clause = f" WHERE w.rn > {v}"
        elif filter_choice == 1:
            where_clause = f" WHERE w.{c} < {v + 2}"
        elif filter_choice == 2:
            where_clause = f" WHERE w.rn BETWEEN {v} AND {v + 5}"
        elif filter_choice == 3:
            where_clause = f" WHERE w.{c} IN ({v}, {v + 1}, {v + 2})"
        elif filter_choice == 4:
            where_clause = f" WHERE w.{c} IS NOT NULL"
        elif filter_choice == 5:
            where_clause = f" WHERE w.a = {v}"
        else:
            where_clause = ""

        form = rng.randint(0, 19)
        if form == 0:
            qs.append(f"SELECT {sel_expr} FROM ({base}) w{where_clause} ORDER BY ALL")
        elif form == 1:
            qs.append(f"SELECT {sel_expr} FROM ({base}) w{where_clause} ORDER BY w.rn DESC LIMIT {rng.randint(2, 10)}")
        elif form == 2:
            qs.append(f"WITH cte AS ({base}) SELECT {sel_expr} FROM cte w{where_clause} ORDER BY ALL")
        elif form == 3:
            qs.append(f"SELECT count(*), max(w.rn) FROM ({base}) w{where_clause}")
        elif form == 4:
            qs.append(f"SELECT avg(w.rn), min(w.{c}) FROM ({base}) w{where_clause}")
        elif form == 5:
            qs.append(f"SELECT w.rn, j.b FROM ({base}) w JOIN {t2} j ON w.a = j.a{where_clause} ORDER BY ALL")
        elif form == 6:
            qs.append(f"SELECT w.rn, j.b FROM ({base}) w LEFT JOIN {t2} j ON w.b = j.b{where_clause} ORDER BY ALL")
        elif form == 7:
            qs.append(f"SELECT w.rn, w2.rn AS rn2 FROM ({base}) w JOIN ({base2}) w2 ON w.a = w2.a{where_clause} ORDER BY ALL")
        elif form == 8:
            qs.append(f"SELECT (w.rn % 2) AS grp, count(*), sum(w.{c}) FROM ({base}) w{where_clause} GROUP BY 1 ORDER BY ALL")
        elif form == 9:
            qs.append(f"SELECT w.{c}, count(*), sum(w.rn) FROM ({base}) w{where_clause} GROUP BY w.{c} HAVING count(*) > 0 ORDER BY ALL")
        elif form == 10:
            qs.append(f"SELECT w.rn, w.{c}, row_number() OVER (PARTITION BY w.{c} ORDER BY w.rn) FROM ({base}) w{where_clause} ORDER BY ALL")
        elif form == 11:
            qs.append(f"SELECT w.rn, w.{c}, rank() OVER (ORDER BY w.{c}) FROM ({base}) w{where_clause} ORDER BY ALL")
        elif form == 12:
            qs.append(f"SELECT w.rn, w.{c}, dense_rank() OVER (ORDER BY w.rn) FROM ({base}) w{where_clause} ORDER BY ALL")
        elif form == 13:
            qs.append(f"SELECT w.rn FROM ({base}) w WHERE w.a IN (SELECT a FROM {t2}) ORDER BY ALL")
        elif form == 14:
            qs.append(f"SELECT w.rn FROM ({base}) w WHERE EXISTS (SELECT 1 FROM {t2} WHERE {t2}.a = w.a) ORDER BY ALL")
        elif form == 15:
            qs.append(f"SELECT DISTINCT (w.rn % 3), w.{c} FROM ({base}) w{where_clause} ORDER BY ALL")
        elif form == 16:
            qs.append(f"SELECT * FROM (SELECT {sel_expr} FROM ({base}) w{where_clause} LIMIT 10) s1 UNION ALL SELECT * FROM (SELECT {sel_expr} FROM ({base}) w{where_clause} LIMIT 10) s2 ORDER BY ALL")
        elif form == 17:
            qs.append(f"SELECT w.rn, w.{c} FROM ({base}) w QUALIFY row_number() OVER (ORDER BY w.rn) <= {v + 1} ORDER BY ALL")
        elif form == 18:
            qs.append(f"SELECT sum(x) FROM (SELECT count(*) AS x FROM ({base}) w{where_clause}) sub")
        else:
            qs.append(f"SELECT 1, count(*) FROM ({base}) w{where_clause} GROUP BY 1 ORDER BY ALL")

    return {"setup": setup, "queries": qs}
