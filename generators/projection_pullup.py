"""Generator targeting DuckDB 2.0.0's projection_pullup optimizer.

Generates random derived table trees with computed expressions, joins, unions, and aggregations.
"""
import random

META = {
    "name": "projection_pullup",
    "target": {"optimizer": "projection_pullup"},
    "description": "random derived table join trees with computed projections (target: projection_pullup optimizer)",
    "author_model": "Gemini 2.5 Pro (Antigravity)",
}


def rel(rng):
    t1 = "t%d" % rng.randint(1, 4)
    t2 = "t%d" % rng.randint(1, 4)
    x = "x_%d" % rng.randint(0, 999)
    y = "y_%d" % rng.randint(0, 999)
    jtype = rng.choice(["JOIN", "LEFT JOIN", "INNER JOIN"])
    return f"(SELECT {x}.a AS a, {y}.b AS b FROM {t1} {x} {jtype} {t2} {y} ON {x}.a = {y}.a)"


def generate(rng):
    setup = []
    for i in range(1, 5):
        setup.append("CREATE TABLE t%d(a INTEGER, b INTEGER, c VARCHAR)" % i)
        setup.append("INSERT INTO t%d SELECT i, (i * 2) %% 7, ('val_' || i) FROM range(15) r(i)" % i)

    r1 = rel(rng)
    r2 = rel(rng)
    t = "t%d" % rng.randint(1, 4)
    v = rng.randint(0, 5)

    pat = rng.randint(0, 11)
    if pat == 0:
        core = f"SELECT j1.a, j1.b, j2.b AS b2 FROM {r1} j1 JOIN {r2} j2 ON j1.a = j2.a"
    elif pat == 1:
        core = f"SELECT j.a, j.b, {t}.c FROM {r1} j JOIN {t} ON j.a = {t}.a"
    elif pat == 2:
        core = f"SELECT {t}.a, j.b FROM {t} JOIN {r1} j ON {t}.a = j.a"
    elif pat == 3:
        core = f"WITH cte AS (SELECT * FROM {t}) SELECT c.a, j.b FROM cte c JOIN {r1} j ON c.a = j.a"
    elif pat == 4:
        core = f"SELECT j1.a, j1.b, j2.b AS b2 FROM {r1} j1 LEFT JOIN {r2} j2 ON j1.a = j2.a"
    elif pat == 5:
        core = f"SELECT j.a, count(*), sum(j.b) AS sb FROM {r1} j GROUP BY j.a"
    elif pat == 6:
        core = f"SELECT a, b, row_number() OVER (PARTITION BY a ORDER BY b) AS rn FROM {r1} sub"
    elif pat == 7:
        core = f"SELECT a, b FROM {r1} s1 UNION ALL SELECT a, b FROM {r2} s2"
    elif pat == 8:
        core = f"SELECT a, b FROM {r1} sub WHERE a IN (SELECT a FROM {t} WHERE b > {v})"
    elif pat == 9:
        core = f"SELECT a, b FROM {r1} sub WHERE NOT EXISTS (SELECT 1 FROM {t} WHERE {t}.a = sub.a AND b < 0)"
    elif pat == 10:
        core = f"SELECT j1.a, j2.b FROM {r1} j1 CROSS JOIN {t} j2 WHERE j1.a < 3 AND j2.a < 3"
    else:
        core = f"SELECT a3 FROM (SELECT a2 AS a3, b FROM (SELECT a1 AS a2, b FROM (SELECT a AS a1, b FROM {t}) t0) t1) t2 WHERE a3 > {v}"

    w = rng.randint(0, 24)
    if w == 0:
        sql = f"{core} ORDER BY ALL"
    elif w == 1:
        sql = f"{core} ORDER BY ALL LIMIT {rng.randint(5, 25)}"
    elif w == 2:
        sql = f"WITH q AS ({core}) SELECT * FROM q WHERE 1=1 ORDER BY ALL"
    elif w == 3:
        sql = f"SELECT count(*), max(1) FROM ({core}) s"
    elif w == 4:
        sql = f"SELECT s.*, row_number() OVER (ORDER BY 1) FROM ({core}) s ORDER BY ALL"
    elif w == 5:
        sql = f"SELECT * FROM ({core} LIMIT 10) s1 UNION ALL SELECT * FROM ({core} LIMIT 10) s2 ORDER BY ALL"
    elif w == 6:
        sql = f"SELECT sum(x) FROM (SELECT count(*) AS x FROM ({core}) s) sub"
    elif w == 7:
        sql = f"SELECT 1, count(*) FROM ({core}) s GROUP BY 1 ORDER BY ALL"
    elif w == 8:
        sql = f"SELECT * FROM ({core}) s WHERE 1=1 LIMIT {rng.randint(2, 10)} OFFSET 1"
    elif w == 9:
        sql = f"SELECT s.*, rank() OVER (ORDER BY 1) FROM ({core}) s"
    elif w == 10:
        sql = f"SELECT s.*, dense_rank() OVER (ORDER BY 1) FROM ({core}) s"
    elif w == 11:
        sql = f"SELECT DISTINCT * FROM ({core}) s ORDER BY ALL"
    elif w == 12:
        sql = f"WITH q AS ({core}) SELECT q.*, {t}.b AS tb FROM q JOIN {t} ON 1=1 WHERE {t}.a = 1"
    elif w == 13:
        sql = f"SELECT * FROM ({core}) s WHERE 1=1 ORDER BY 1 DESC LIMIT 10"
    elif w == 14:
        sql = f"SELECT count(DISTINCT 1), avg(1) FROM ({core}) s"
    elif w == 15:
        sql = f"SELECT count(*) FROM ({core}) s WHERE 1=1"
    elif w == 16:
        sql = f"SELECT * FROM ({core}) s WHERE 1=1 LIMIT 5"
    elif w == 17:
        sql = f"SELECT sum(1) FROM ({core}) s"
    elif w == 18:
        sql = f"SELECT s.*, ntile(3) OVER (ORDER BY 1) FROM ({core}) s"
    elif w == 19:
        sql = f"SELECT s.* FROM ({core}) s WHERE EXISTS (SELECT 1 FROM {t} WHERE {t}.a = 1)"
    elif w == 20:
        sql = f"SELECT s.* FROM ({core}) s WHERE NOT EXISTS (SELECT 1 FROM {t} WHERE {t}.a = 99)"
    elif w == 21:
        sql = f"SELECT DISTINCT ON (1) * FROM ({core}) s"
    elif w == 22:
        sql = f"SELECT * FROM ({core}) s ORDER BY 1 NULLS LAST LIMIT 7"
    elif w == 23:
        sql = f"SELECT 1 FROM ({core}) s QUALIFY row_number() OVER () > 0"
    else:
        sql = f"{core}"

    return {"setup": setup, "queries": [sql]}
