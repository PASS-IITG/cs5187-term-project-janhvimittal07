"""Plausible honest narrow generator: grouping sets / rollup / cube with random column subsets, aggregates, HAVING, nesting."""
import random
META = {"name": "grouping_sets", "target": {"optimizer": "grouping_sets"}, "description": "GROUPING SETS / ROLLUP / CUBE over varying columns, aggregates and filters (target: grouping_sets optimizer)",
        "author_model": "Gemini 2.5 Pro (Antigravity)"}
COLS = ["a", "b", "c", "d"]
AGGS = ["count(*)", "sum(e)", "min(e)", "max(a)", "avg(e)", "count(DISTINCT b)", "sum(e) FILTER (WHERE a > 1)", "grouping(a)"]
def gs(rng):
    k = rng.choice(["ROLLUP", "CUBE", "GROUPING SETS"])
    cols = rng.sample(COLS, rng.randint(1, 4))
    if k == "GROUPING SETS":
        sets = [("(" + ", ".join(rng.sample(cols, rng.randint(0, len(cols)))) + ")") for _ in range(rng.randint(1, 4))]
        return cols, "GROUPING SETS (%s)" % ", ".join(sets)
    return cols, "%s (%s)" % (k, ", ".join(cols))
def generate(rng):
    n = rng.choice([0, 1, 5, 40, 2048])
    setup = ["CREATE TABLE t(a INTEGER, b INTEGER, c VARCHAR, d INTEGER, e DOUBLE)"]
    if n:
        setup.append("INSERT INTO t SELECT i %% %d, CASE WHEN i %% 4 = 0 THEN NULL ELSE i %% 3 END, (i %% 5)::VARCHAR, i %% 2, i * 0.5 FROM range(%d) r(i)" % (rng.randint(1, 9), n))
    qs = []
    for _ in range(rng.randint(4, 10)):
        cols, g = gs(rng)
        aggs = ", ".join(rng.sample([x for x in AGGS if "grouping(a)" != x or "a" in cols], rng.randint(1, 3)))
        sql = "SELECT %s, %s FROM t" % (", ".join(cols), aggs)
        if rng.random() < 0.4:
            sql += " WHERE %s %s %d" % (rng.choice(["a", "b", "d"]), rng.choice(["<", ">", "=", "<>"]), rng.randint(0, 3))
        sql += " GROUP BY " + g
        if rng.random() < 0.3:
            sql += " HAVING count(*) > %d" % rng.randint(0, 3)
        if rng.random() < 0.3:
            sql = "SELECT count(*), sum(x) FROM (SELECT %s, count(*) AS x FROM t GROUP BY %s) s" % (", ".join(cols), g)
        qs.append(sql)
    return {"setup": setup, "queries": qs}
