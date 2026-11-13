"""Generator targeting DuckDB 2.0.0's projection_placement optimizer.

Stresses cost-aware placement of computed projections around joins (e.g. bounded substrings,
length calculations, string functions on dimension vs fact side of fanouts).
"""
import random

META = {
    "name": "projection_placement",
    "target": {"optimizer": "projection_placement"},
    "description": "cost-aware placement of computed string and arithmetic projections across join fanouts",
    "author_model": "Gemini 2.5 Pro (Antigravity)",
}


def generate(rng):
    setup = [
        "CREATE TABLE placement_dim AS SELECT i::INTEGER AS i, (i % 10)::INTEGER AS k, lpad(i::VARCHAR, 50, 'x') AS v FROM range(20) t(i)",
        "CREATE TABLE placement_dim2 AS SELECT i::INTEGER AS i, (i % 5)::INTEGER AS grp, ('tag_' || i)::VARCHAR AS tag FROM range(20) t(i)",
        "CREATE TABLE placement_fact AS SELECT (i % 20)::INTEGER AS i, (i % 7)::INTEGER AS f_val, i::INTEGER AS id FROM range(200) t(i)",
        "CREATE TABLE placement_fact2 AS SELECT (i % 20)::INTEGER AS i, (i % 9)::INTEGER AS score, i::INTEGER AS id FROM range(100) t(i)",
        "ANALYZE",
    ]

    qs = []
    num_queries = rng.randint(1, 2)
    for _ in range(num_queries):
        fn = rng.choice([
            f"substring(v, 1, {rng.randint(5, 20)})",
            f"substring(v, 2, {rng.randint(5, 20)})",
            f"substring(v, -{rng.randint(1, 10)}, {rng.randint(5, 20)})",
            f"substring_grapheme(v, 1, {rng.randint(5, 20)})",
        ])
        jtype = rng.choice(["JOIN", "LEFT JOIN", "INNER JOIN"])
        thresh = rng.randint(0, 4)

        core_shape = rng.randint(0, 6)
        if core_shape == 0:
            core = f"SELECT d.s, f.f_val FROM (SELECT i, {fn} AS s FROM placement_dim) d {jtype} placement_fact f USING (i) WHERE f.f_val > {thresh}"
        elif core_shape == 1:
            core = f"SELECT d.s, f.f_val, f.id FROM (SELECT i, {fn} AS s FROM placement_dim) d {jtype} placement_fact f USING (i) WHERE f.f_val > {thresh}"
        elif core_shape == 2:
            core = f"SELECT d.s, f.f_val, f2.score FROM (SELECT i, {fn} AS s FROM placement_dim) d {jtype} placement_fact f USING (i) JOIN placement_fact2 f2 USING (i) WHERE f.f_val > {thresh}"
        elif core_shape == 3:
            core = f"SELECT d.s, f.f_val, d2.tag FROM (SELECT i, {fn} AS s FROM placement_dim) d {jtype} placement_fact f USING (i) JOIN placement_dim2 d2 USING (i) WHERE f.f_val > {thresh}"
        elif core_shape == 4:
            core = f"SELECT f.f_val, d.s FROM placement_fact f {jtype} (SELECT i, {fn} AS s FROM placement_dim) d USING (i) WHERE f.f_val > {thresh}"
        elif core_shape == 5:
            core = f"SELECT d.s, f.f_val, row_number() OVER (PARTITION BY d.i ORDER BY f.f_val) AS rn FROM (SELECT i, {fn} AS s FROM placement_dim) d {jtype} placement_fact f USING (i) WHERE f.f_val > {thresh}"
        else:
            core = f"SELECT d.s, f.f_val FROM (SELECT i, {fn} AS s FROM placement_dim) d {jtype} placement_fact f USING (i) WHERE EXISTS (SELECT 1 FROM placement_dim2 d2 WHERE d2.i = d.i)"

        form = rng.randint(0, 24)
        if form == 0:
            sql = f"{core} ORDER BY ALL"
        elif form == 1:
            sql = f"WITH q AS ({core}) SELECT * FROM q WHERE 1=1 ORDER BY ALL"
        elif form == 2:
            sql = f"SELECT s, count(*) FROM ({core}) sub GROUP BY s"
        elif form == 3:
            sql = f"SELECT s, count(*), sum(f_val) FROM ({core}) sub GROUP BY s ORDER BY ALL"
        elif form == 4:
            sql = f"SELECT s, f_val, count(*) FROM ({core}) sub GROUP BY s, f_val HAVING count(*) > 1 ORDER BY ALL"
        elif form == 5:
            sql = f"SELECT * FROM ({core}) s WHERE 1=1 ORDER BY ALL LIMIT {rng.randint(10, 50)}"
        elif form == 6:
            sql = f"SELECT s.*, row_number() OVER (ORDER BY 1) FROM ({core}) s ORDER BY ALL"
        elif form == 7:
            sql = f"SELECT * FROM ({core} LIMIT 10) s1 UNION ALL SELECT * FROM ({core} LIMIT 10) s2 ORDER BY ALL"
        elif form == 8:
            sql = f"SELECT s, sum(f_val) FROM ({core}) s GROUP BY s"
        elif form == 9:
            sql = f"WITH q AS ({core}) SELECT q.*, d.tag FROM q JOIN placement_dim2 d ON 1=1 WHERE d.i = 1 ORDER BY ALL"
        elif form == 10:
            sql = f"SELECT s.*, rank() OVER (ORDER BY f_val) FROM ({core}) s ORDER BY ALL"
        elif form == 11:
            sql = f"SELECT s.*, dense_rank() OVER (ORDER BY f_val) FROM ({core}) s ORDER BY ALL"
        elif form == 12:
            sql = f"SELECT DISTINCT * FROM ({core}) s ORDER BY ALL"
        elif form == 13:
            sql = f"SELECT * FROM ({core}) s WHERE 1=1 ORDER BY 1 DESC LIMIT 10"
        elif form == 14:
            sql = f"SELECT count(DISTINCT s), avg(f_val) FROM ({core}) s"
        elif form == 15:
            sql = f"SELECT s, min(f_val), max(f_val) FROM ({core}) s GROUP BY s HAVING count(*) > 0 ORDER BY ALL"
        elif form == 16:
            sql = f"SELECT * FROM ({core}) s WHERE 1=1 LIMIT 5 OFFSET 2"
        elif form == 17:
            sql = f"SELECT s.*, f2.score FROM ({core}) s JOIN placement_fact2 f2 ON s.f_val = f2.score LIMIT 10"
        elif form == 18:
            sql = f"SELECT s, f_val, count(*) FROM ({core}) s GROUP BY s, f_val ORDER BY ALL"
        elif form == 19:
            sql = f"SELECT s.*, lag(s, 1) OVER (ORDER BY f_val) FROM ({core}) s ORDER BY ALL"
        elif form == 20:
            sql = f"SELECT s.* FROM ({core}) s WHERE EXISTS (SELECT 1 FROM placement_dim2 d WHERE d.grp = s.f_val)"
        elif form == 21:
            sql = f"SELECT DISTINCT ON (1) * FROM ({core}) s"
        elif form == 22:
            sql = f"SELECT s, count(*) FROM ({core}) sub GROUP BY GROUPING SETS ((s), ())"
        elif form == 23:
            sql = f"SELECT 1 FROM ({core}) s QUALIFY row_number() OVER () > 0"
        else:
            sql = f"{core}"
        qs.append(sql)

    return {"setup": setup, "queries": qs}
