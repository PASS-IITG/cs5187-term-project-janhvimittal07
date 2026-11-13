"""Generator targeting DuckDB 2.0.0's redundant_order_keys optimizer.

Stresses removal of redundant ORDER BY keys that follow unique/primary key prefixes,
composite primary keys, join ordering, interactions with filters, limits, and joins.
"""
import random

META = {
    "name": "redundant_order_keys",
    "target": {"optimizer": "redundant_order_keys"},
    "description": "removal of redundant order-by keys following unique prefixes across single/composite keys and joins",
    "author_model": "Gemini 2.5 Pro (Antigravity)",
}

COLS = ["payload", "grp", "extra"]


def generate(rng):
    n = rng.choice([5, 20, 50, 150])
    setup = [
        "DROP TABLE IF EXISTS comp",
        "DROP TABLE IF EXISTS t",
        "DROP TABLE IF EXISTS t2",
        "CREATE TABLE t(id INTEGER PRIMARY KEY, payload VARCHAR, grp INTEGER, extra DOUBLE)",
        f"INSERT INTO t SELECT i, ('txt_' || (i % 7)), i % 4, i * 0.5 FROM range({n}) r(i)",
        "CREATE TABLE comp(p INTEGER, s INTEGER, val VARCHAR, PRIMARY KEY(p, s))",
        f"INSERT INTO comp SELECT i % 5, i / 5, ('val_' || i) FROM range({n}) r(i)",
        "CREATE TABLE t2(id INTEGER, score INTEGER)",
        f"INSERT INTO t2 SELECT i, (i * 3) % 10 FROM range({n}) r(i)",
    ]

    qs = []
    num_queries = rng.randint(1, 2)
    for _ in range(num_queries):
        style = rng.randint(0, 4)
        if style == 0:
            pre = rng.choice([[], ["val"]])
            order_cols = pre + ["p", "s", "val"]
            order_clause = " ORDER BY " + ", ".join(f"{c} {rng.choice(['ASC', 'DESC'])}" for c in order_cols)
            core = f"SELECT p, s, val FROM comp{order_clause}"
        elif style == 1:
            pre = rng.sample(COLS, rng.randint(0, 1))
            post = rng.sample(COLS, rng.randint(1, 2))
            order_cols = pre + ["id"] + post
            order_clause = " ORDER BY " + ", ".join(f"{c} {rng.choice(['ASC', 'DESC'])}" for c in order_cols)
            where_clause = f" WHERE id > {rng.randint(0, 5)}" if rng.random() < 0.4 else ""
            sel = rng.choice(["id", "id, payload", "id, grp, extra", "*"])
            core = f"SELECT {sel} FROM t{where_clause}{order_clause}"
        elif style == 2:
            order_clause = f" ORDER BY t.id, t.payload {rng.choice(['ASC', 'DESC'])}"
            jtype = rng.choice(["JOIN", "LEFT JOIN"])
            core = f"SELECT t.id, t.payload, c.val FROM t {jtype} comp c ON t.id = c.p{order_clause}"
        elif style == 3:
            order_clause = f" ORDER BY grp, min(extra), max(payload)"
            core = f"SELECT grp, count(*), sum(extra) FROM t GROUP BY grp{order_clause}"
        else:
            order_clause = f" ORDER BY id, payload"
            core = f"SELECT id, payload, extra FROM t WHERE extra > {rng.randint(0, 10)}{order_clause}"

        form = rng.randint(0, 19)
        if form == 0:
            qs.append(f"{core}")
        elif form == 1:
            qs.append(f"{core} LIMIT {rng.randint(2, 20)}")
        elif form == 2:
            qs.append(f"WITH q AS ({core}) SELECT * FROM q WHERE 1=1 ORDER BY 1")
        elif form == 3:
            qs.append(f"SELECT count(*), max(1) FROM ({core}) sub")
        elif form == 4:
            qs.append(f"SELECT * FROM ({core}) s WHERE 1=1 LIMIT {rng.randint(5, 30)} OFFSET 1")
        elif form == 5:
            qs.append(f"SELECT s.*, row_number() OVER (ORDER BY 1) FROM ({core}) s")
        elif form == 6:
            qs.append(f"SELECT * FROM ({core} LIMIT 10) s1 UNION ALL SELECT * FROM ({core} LIMIT 10) s2")
        elif form == 7:
            qs.append(f"SELECT sum(x) FROM (SELECT count(*) AS x FROM ({core}) s) sub")
        elif form == 8:
            qs.append(f"WITH q AS ({core}) SELECT q.* FROM q JOIN comp c ON 1=1 WHERE c.p = 1")
        elif form == 9:
            qs.append(f"SELECT 1, count(*) FROM ({core}) s GROUP BY 1")
        elif form == 10:
            qs.append(f"SELECT s.*, rank() OVER (ORDER BY 1) FROM ({core}) s")
        elif form == 11:
            qs.append(f"SELECT s.*, dense_rank() OVER (ORDER BY 1) FROM ({core}) s")
        elif form == 12:
            qs.append(f"WITH q AS ({core}) SELECT q.*, t2.score FROM q LEFT JOIN t2 ON 1=1 WHERE t2.id = 1")
        elif form == 13:
            qs.append(f"SELECT DISTINCT * FROM ({core}) s")
        elif form == 14:
            qs.append(f"SELECT * FROM ({core}) s WHERE 1=1 ORDER BY 1 DESC LIMIT 10")
        elif form == 15:
            qs.append(f"SELECT count(DISTINCT 1), avg(1) FROM ({core}) s")
        elif form == 16:
            qs.append(f"SELECT s.* FROM ({core}) s JOIN t2 ON 1=1 WHERE t2.score > 5 LIMIT 10")
        elif form == 17:
            qs.append(f"SELECT 1 AS k, count(*) FROM ({core}) s GROUP BY 1 HAVING count(*) > 0")
        elif form == 18:
            qs.append(f"SELECT * FROM ({core}) s WHERE 1=1 LIMIT 5")
        else:
            qs.append(f"SELECT * FROM ({core}) s WHERE 1=1")

    return {"setup": setup, "queries": qs}
