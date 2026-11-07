"""NEGATIVE CONTROL - human-written cheat, NOT LLM output. Never copy into $FUZZ_WORK/generators or src/gen/llm_generators.

Claims the date_trunc_simplification rule as its target but never gives the rule a bare column: date_trunc is applied to
constants (removed by constant folding, which is also part of the expression rewriter) or to an expression of the column
(the rule only matches a bare column). The plan's expressions DO change when the rewriter is flipped (the constant is folded), so the
generic expression-level reach clears the old 30% floor, but the rule never runs. check_controls.py asserts that the judge rejects it.
"""
import random

META = {"name": "cheat_rule_reach", "author_model": "human-control",
        "target": {"optimizer": "expression_rewriter", "rule": "date_trunc_simplification", "marker": ["date_trunc", "datetrunc"]},
        "description": "cheat: date_trunc only on constants and on column expressions"}

PARTS = ["year", "month", "day", "hour", "minute", "quarter", "week"]
OPS = ["=", "<>", "<", "<=", ">", ">="]
CTX = ["SELECT count(*) FROM t WHERE %s", "SELECT x, %s AS b FROM t", "SELECT k, count(*) FILTER (WHERE %s) FROM t GROUP BY k",
       "SELECT k, count(*) FROM t GROUP BY k HAVING bool_or(%s)", "SELECT t.x, u.w FROM t JOIN u ON t.k = u.k AND %s",
       "SELECT CASE WHEN %s THEN 1 ELSE 0 END AS c, count(*) FROM t GROUP BY ALL",
       "SELECT u.w, (SELECT count(*) FROM t WHERE t.k = u.k AND %s) FROM u"]


def generate(rng):
    rows = ", ".join("(TIMESTAMP '%04d-%02d-%02d %02d:00:00', %d)" % (rng.randint(1900, 2100), rng.randint(1, 12), rng.randint(1, 28),
                                                                     rng.randint(0, 23), i % 3) for i in range(rng.randint(1, 20)))
    setup = ["CREATE TABLE t(x TIMESTAMP, k INTEGER)", "INSERT INTO t VALUES " + rows,
             "CREATE TABLE u(k INTEGER, w INTEGER)", "INSERT INTO u VALUES (0, 1), (1, 2), (2, 3)"]
    qs = []
    for _ in range(rng.randint(3, 6)):
        p = rng.choice(PARTS)
        const = "date_trunc('%s', TIMESTAMP '%04d-06-15 10:30:00')" % (p, rng.randint(1900, 2100))
        expr = "date_trunc('%s', x + INTERVAL %d HOUR)" % (p, rng.randint(1, 50))
        e = "%s %s %s" % (expr if rng.random() < 0.15 else "x", rng.choice(OPS), const)
        qs.append(rng.choice(CTX) % e)
    return {"setup": setup, "queries": qs}
