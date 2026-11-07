"""POSITIVE CONTROL - human-written, NOT LLM output. Never copy into $FUZZ_WORK/generators or src/gen/llm_generators.

The hand-written differential that found duckdb/duckdb issue 28527 (date_trunc with a sub-day part on a DATE column, wrong
result from DateTruncSimplificationRule), rewritten in the generator contract. harness/controls/check_controls.py runs it
end to end and asserts that the pipeline (rule-level reach -> config_diff hit -> verify_diff -> registry) classifies the
hits as a REDISCOVERY of 28527. If that ever stops being true, the pipeline lost the ability to find this bug class.
Kept out of every LLM-visible directory so it cannot leak the answer to the generator-writing agent.
"""
import random

META = {"name": "control_date_trunc", "author_model": "human-control",
        "target": {"optimizer": "expression_rewriter", "rule": "date_trunc_simplification", "marker": ["date_trunc", "datetrunc"]},
        "description": "date_trunc(part, col) OP const under the expression rewriter (DateTruncSimplificationRule)"}

PARTS = ["millennium", "century", "decade", "year", "quarter", "month", "week", "isoyear", "day", "hour", "minute", "second",
         "milliseconds", "microseconds"]
TYPES = ["DATE", "TIMESTAMP", "TIMESTAMPTZ", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS"]
OPS = ["=", "<>", "<", "<=", ">", ">=", "IS NOT DISTINCT FROM", "IS DISTINCT FROM"]


def lit(rng):
    y = rng.choice([1, 2, 100, 1582, 1969, 1970, 2000, 2024, rng.randint(1, 3000)])
    s = "%04d-%02d-%02d" % (y, rng.randint(1, 12), rng.randint(1, 28))
    if rng.random() < 0.6:
        s += " %02d:%02d:%02d" % (rng.randint(0, 23), rng.choice([0, rng.randint(0, 59)]), rng.choice([0, rng.randint(0, 59)]))
    return rng.choice([s, s, "infinity", "-infinity"])


def generate(rng):
    t = rng.choice(TYPES)
    vals = [lit(rng) for _ in range(rng.randint(1, 30))]
    rows = ["CAST('%s' AS %s)" % (v, t) for v in vals] + ["NULL"] * rng.randint(0, 2)
    setup = ["CREATE TABLE t(x %s, k INTEGER)" % t,
             "INSERT INTO t VALUES " + ", ".join("(%s, %d)" % (r, i % 3) for i, r in enumerate(rows)),
             "CREATE TABLE u(k INTEGER, w INTEGER)", "INSERT INTO u VALUES (0, 1), (1, 2), (2, 3), (NULL, 4)"]
    qs = []
    for _ in range(rng.randint(3, 6)):
        p = rng.choice(PARTS)
        # boundary constants: a value that is IN the data (so = can match, < / > sit on a bucket edge), sometimes shifted
        base = rng.choice(vals + [lit(rng)])
        c = "CAST('%s' AS %s)" % (base, rng.choice(TYPES + [t, t, t]))
        if rng.random() < 0.4:
            c = "date_trunc('%s', %s)" % (p, c)
        op = rng.choice(OPS)
        e = "date_trunc('%s', x) %s %s" % (p, op, c) if rng.random() < 0.5 else "%s %s date_trunc('%s', x)" % (c, op, p)
        ctx = rng.randrange(7)
        if ctx == 0:
            q = "SELECT count(*), sum(hash(x)) FROM t WHERE " + e
        elif ctx == 1:
            q = "SELECT x, %s AS b FROM t" % e
        elif ctx == 2:
            q = "SELECT k, count(*) FILTER (WHERE %s) FROM t GROUP BY k" % e
        elif ctx == 3:
            q = "SELECT k, count(*) FROM t GROUP BY k HAVING bool_or(%s)" % e
        elif ctx == 4:
            q = "SELECT t.x, u.w FROM t JOIN u ON t.k = u.k AND %s" % e
        elif ctx == 5:
            q = "SELECT CASE WHEN %s THEN 1 WHEN x IS NULL THEN 2 ELSE 0 END AS c, count(*) FROM t GROUP BY ALL" % e
        else:
            q = "SELECT u.w, (SELECT count(*) FROM t WHERE t.k = u.k AND %s) FROM u" % e
        qs.append(q)
    return {"setup": setup, "queries": qs}
