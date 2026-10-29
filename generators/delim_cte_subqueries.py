"""Correlated-subquery shapes aimed at the delim-join-to-CTE rewrite (new in 2.0, setting delim_join_as_cte).

Every correlated subquery is a dependent join; 2.0 flattens it and, by default, rewrites the delim joins into materialized
CTEs (src/planner/subquery/delim_join_cte_rewriter.cpp, flatten_dependent_join.cpp). The generator varies WHERE the subquery
sits (select list, WHERE, HAVING, ORDER BY, JOIN ON, CASE, LATERAL), WHAT it computes (aggregates, EXISTS/IN/ANY/ALL, windows,
set operations, DISTINCT) and how it is NESTED (two levels, the inner one referencing the OUTERMOST row).
v2: numeric subqueries only where a number is needed (v1 mixed VARCHAR/INTEGER), fixed alias scoping, more shapes.
"""
import random

META = {
    "name": "delim_cte_subqueries",
    "target": {"setting": "delim_join_as_cte"},
    "description": "correlated subqueries in every clause, nested, with aggregates / windows / set ops; dependent-join flattening",
    "author_model": "Gemini 2.5 Pro (Antigravity)",
}


def _val(rng, kind):
    if rng.random() < 0.18:
        return "NULL"
    if kind == "str":
        return "'" + rng.choice(["a", "b", "c", "", "aa"]) + "'"
    return str(rng.choice([0, 1, 2, 3, 5, -1, 10]))


def _table(rng, name, n_rows):
    rows = ", ".join("(%s, %s, %s)" % (_val(rng, "int"), _val(rng, "int"), _val(rng, "str")) for _ in range(n_rows))
    out = ["CREATE TABLE %s(a INTEGER, b INTEGER, c VARCHAR)" % name]
    if n_rows:
        out.append("INSERT INTO %s VALUES %s" % (name, rows))
    return out


NUM_AGGS = ["count(*)", "sum({i}.b)", "min({i}.b)", "max({i}.a)", "count(DISTINCT {i}.c)", "avg({i}.a)", "count({i}.a) FILTER (WHERE {i}.b > 1)"]
OTHER_AGGS = ["string_agg({i}.c, ',' ORDER BY {i}.c)", "bool_or({i}.b > 1)", "list({i}.a ORDER BY {i}.a)"]


def _corr(rng, inner, outer):
    return rng.choice([
        "{i}.a = {o}.a", "{i}.b = {o}.b", "{i}.a < {o}.b", "{i}.a = {o}.a AND {i}.c = {o}.c", "{i}.a IS NOT DISTINCT FROM {o}.a",
        "{i}.b + {o}.a > {o}.a", "({i}.a = {o}.a OR {i}.b IS NULL)", "{i}.c = {o}.c", "{i}.a = {o}.b",
    ]).format(i=inner, o=outer)


def _num_sub(rng, outer, depth, tables):
    """A scalar subquery returning a number (or NULL)."""
    inner = "i%d" % depth
    t = rng.choice(tables)
    where = _corr(rng, inner, outer)
    if depth < 3 and rng.random() < 0.4:                       # nested: the deeper level may reference the outermost row
        ref = rng.choice([inner, outer, "o"])
        where += " AND %s.b <= COALESCE(%s, 99)" % (inner, _num_sub(rng, ref, depth + 1, tables))
    s = rng.random()
    agg = rng.choice(NUM_AGGS).format(i=inner)
    if s < 0.5:
        return "(SELECT %s FROM %s %s WHERE %s)" % (agg, t, inner, where)
    if s < 0.62:
        return "(SELECT max(g) FROM (SELECT %s AS g FROM %s %s WHERE %s GROUP BY %s.c))" % (agg, t, inner, where, inner)
    if s < 0.74:
        return "(SELECT max(w) FROM (SELECT row_number() OVER (PARTITION BY %s.c ORDER BY %s.a, %s.b) AS w FROM %s %s WHERE %s))" % (
            inner, inner, inner, t, inner, where)
    if s < 0.84:
        return "(SELECT count(*) FROM (SELECT %s.a AS x FROM %s %s WHERE %s UNION ALL SELECT k.b FROM %s k WHERE k.a = %s.a))" % (
            inner, t, inner, where, rng.choice(tables), outer)
    if s < 0.92:
        return "(SELECT sum(x) FROM (SELECT DISTINCT %s.a AS x FROM %s %s WHERE %s))" % (inner, t, inner, where)
    return "(SELECT %s FROM %s %s WHERE %s HAVING count(*) >= 0)" % (agg, t, inner, where)      # HAVING without GROUP BY


def _any_sub(rng, outer, tables):
    inner = "q"
    t = rng.choice(tables)
    return "(SELECT %s FROM %s q WHERE %s)" % (rng.choice(NUM_AGGS + OTHER_AGGS).format(i=inner), t, _corr(rng, inner, outer))


def _pred_sub(rng, outer, tables):
    t = rng.choice(tables)
    w = _corr(rng, "p", outer)
    return rng.choice([
        "EXISTS (SELECT 1 FROM %s p WHERE %s)" % (t, w),
        "NOT EXISTS (SELECT 1 FROM %s p WHERE %s)" % (t, w),
        "%s.a IN (SELECT p.a FROM %s p WHERE %s)" % (outer, t, w),
        "%s.b NOT IN (SELECT p.b FROM %s p WHERE %s)" % (outer, t, w),
        "%s.a > ANY (SELECT p.b FROM %s p WHERE %s)" % (outer, t, w),
        "%s.a <= ALL (SELECT p.b FROM %s p WHERE %s)" % (outer, t, w),
        "COALESCE(%s, 0) > %s.a" % (_num_sub(rng, outer, 1, tables), outer),
        "EXISTS (SELECT 1 FROM %s p WHERE %s AND p.a > COALESCE(%s, -1))" % (t, w, _num_sub(rng, "p", 2, tables)),
    ])


def generate(rng):
    tables = ["t1", "t2", "t3"]
    setup = []
    for t in tables:
        setup += _table(rng, t, rng.choice([0, 1, 3, 6, 12, 30]))
    o = "o"
    queries = []
    for _ in range(rng.randint(5, 10)):
        place = rng.choice(["select", "select", "where", "where", "having", "order", "joinon", "case", "lateral", "pred_in_select",
                            "groupby", "setop", "cte_ref"])
        if place == "select":
            sql = "SELECT %s.a, %s AS s FROM t1 %s" % (o, _any_sub(rng, o, tables), o)
        elif place == "where":
            sql = "SELECT %s.a, %s.b FROM t1 %s WHERE %s" % (o, o, o, _pred_sub(rng, o, tables))
        elif place == "having":
            sql = ("SELECT %s.c, count(*) FROM t1 %s GROUP BY %s.c HAVING count(*) > 0 AND %s.c IN "
                   "(SELECT p.c FROM %s p WHERE p.a = max(%s.a))") % (o, o, o, o, rng.choice(tables), o)
        elif place == "order":
            sql = "SELECT %s.a, %s.b FROM t1 %s ORDER BY COALESCE(%s, 0), %s.a, %s.b" % (o, o, o, _num_sub(rng, o, 1, tables), o, o)
        elif place == "joinon":
            sql = "SELECT %s.a, j.b FROM t1 %s %s JOIN t2 j ON %s AND EXISTS (SELECT 1 FROM t3 q WHERE q.a = j.a)" % (
                o, o, rng.choice(["LEFT", "INNER", "FULL"]), _corr(rng, "j", o))
        elif place == "case":
            sql = "SELECT %s.a, CASE WHEN %s THEN COALESCE(%s, 0) ELSE %s END FROM t1 %s" % (
                o, _pred_sub(rng, o, tables), _num_sub(rng, o, 1, tables), _val(rng, "int"), o)
        elif place == "lateral":
            sql = "SELECT %s.a, l.s FROM t1 %s, LATERAL (SELECT %s AS s FROM t2 i1 WHERE %s) l" % (
                o, o, rng.choice(NUM_AGGS + OTHER_AGGS).format(i="i1"), _corr(rng, "i1", o))
        elif place == "pred_in_select":
            sql = "SELECT %s.a, %s AS e FROM t1 %s" % (o, _pred_sub(rng, o, tables), o)
        elif place == "setop":
            sql = "SELECT %s.a FROM t1 %s WHERE %s %s SELECT x.a FROM t2 x WHERE %s" % (
                o, o, _pred_sub(rng, o, tables), rng.choice(["UNION ALL", "EXCEPT", "INTERSECT", "UNION"]), _pred_sub(rng, "x", tables))
        elif place == "cte_ref":
            sql = "WITH w AS (SELECT a, b, c FROM t1) SELECT %s.a, %s AS s FROM w %s" % (o, _num_sub(rng, o, 1, tables), o)
        else:
            sql = "SELECT %s.a, %s AS n, count(*) FROM t1 %s GROUP BY %s.a" % (o, _num_sub(rng, o, 1, tables), o, o)
        queries.append(sql)
    return {"setup": setup, "queries": queries}
