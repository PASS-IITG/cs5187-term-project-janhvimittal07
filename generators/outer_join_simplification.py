"""Generator targeting DuckDB 2.0.0's outer_join_simplification optimizer.

Stresses outer joins (LEFT, RIGHT, FULL) with predicates that allow simplification to INNER JOIN,
null-rejecting filters at different levels of derived tables, and interactions with COALESCE.
"""
import random

META = {
    "name": "outer_join_simplification",
    "target": {"optimizer": "outer_join_simplification"},
    "description": "outer join simplification: varying join types, null-rejecting predicates, nesting depth",
    "author_model": "Gemini 2.5 Pro (Antigravity)",
}

PREDS = [
    "{c} IS NOT NULL",
    "{c} > {v}",
    "{c} < {v}",
    "{c} = {v}",
    "{c} <> {v}",
    "{c} >= {v}",
    "{c} <= {v}",
    "{c} BETWEEN {v} AND {v} + 5",
]


def generate(rng):
    setup = []
    for i in range(1, 5):
        setup.append("CREATE TABLE t%d(a INTEGER, b INTEGER, c INTEGER)" % i)
        n = rng.choice([5, 15, 40])
        setup.append(
            "INSERT INTO t%d SELECT i %% %d, CASE WHEN i %% 3 = 0 THEN NULL ELSE i %% %d END, i FROM range(%d) r(i)"
            % (i, rng.randint(1, 7), rng.randint(1, 5), n)
        )

    qs = []
    for _ in range(rng.randint(1, 2)):
        t1_num = rng.randint(1, 4)
        t2_num = rng.choice([x for x in [1, 2, 3, 4] if x != t1_num])
        t1 = f"t{t1_num}"
        t2 = f"t{t2_num}"
        jtype = "FULL OUTER JOIN"
        on = f"{t1}.a = {t2}.a"
        if rng.random() < 0.3:
            on += f" AND {t1}.b = {t2}.b"

        j_sql = f"{t1} {jtype} {t2} ON {on}"

        col = rng.choice([f"{t1}.a", f"{t1}.b", f"{t2}.a", f"{t2}.c"])
        v = rng.randint(0, 5)
        pred = rng.choice(PREDS).format(c=col, v=v)

        core = f"SELECT {t1}.a AS a1, {t1}.b AS b1, {t2}.a AS a2, {t2}.c AS c2 FROM {j_sql} WHERE {pred}"

        w = rng.randint(0, 15)
        if w == 0:
            qs.append(f"{core} ORDER BY ALL")
        elif w == 1:
            qs.append(f"{core} ORDER BY ALL LIMIT {rng.randint(5, 20)}")
        elif w == 2:
            qs.append(f"WITH q AS ({core}) SELECT * FROM q WHERE 1=1 ORDER BY ALL")
        elif w == 3:
            qs.append(f"SELECT count(*), max(1) FROM ({core}) s")
        elif w == 4:
            qs.append(f"SELECT s.*, row_number() OVER (ORDER BY 1) FROM ({core}) s ORDER BY ALL")
        elif w == 5:
            qs.append(f"SELECT * FROM ({core} LIMIT 10) s1 UNION ALL SELECT * FROM ({core} LIMIT 10) s2 ORDER BY ALL")
        elif w == 6:
            qs.append(f"SELECT sum(x) FROM (SELECT count(*) AS x FROM ({core}) s) sub")
        elif w == 7:
            qs.append(f"SELECT 1, count(*) FROM ({core}) s GROUP BY 1 ORDER BY ALL")
        elif w == 8:
            qs.append(f"SELECT * FROM ({core}) s WHERE 1=1 LIMIT {rng.randint(2, 10)} OFFSET 1")
        elif w == 9:
            qs.append(f"SELECT a1, count(*) FROM ({core}) s GROUP BY a1 HAVING count(*) > 0 ORDER BY ALL")
        elif w == 10:
            qs.append(f"SELECT a1, sum(b1) FROM ({core}) s GROUP BY a1 ORDER BY ALL")
        elif w == 11:
            qs.append(f"SELECT DISTINCT a1, a2 FROM ({core}) s ORDER BY ALL")
        elif w == 12:
            qs.append(f"WITH q AS ({core}) SELECT q.*, t3.b FROM q JOIN t3 ON 1=1 WHERE t3.a = 1 ORDER BY ALL")
        elif w == 13:
            qs.append(f"SELECT * FROM ({core}) s WHERE b1 IS NOT NULL ORDER BY ALL")
        elif w == 14:
            qs.append(f"SELECT count(DISTINCT a1), min(c2) FROM ({core}) s")
        else:
            qs.append(f"{core}")

    return {"setup": setup, "queries": qs}
