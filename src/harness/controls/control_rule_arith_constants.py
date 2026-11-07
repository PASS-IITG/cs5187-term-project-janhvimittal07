"""REDISCOVERY CONTROL for upstream 28535 (not LLM-loop output; never copy into $FUZZ_WORK/generators).
Arithmetic with constants folded or moved by the expression rewriter: MoveConstantsRule (x + c1 OP c2 -> x OP c2 - c1,
c1 - x OP c2, x * c1 OP c2), ConstantOrderNormalization / constant folding ((x + c1) + c2 -> x + (c1 + c2)), arithmetic
simplification (x + 0, x * 1, x - 0, x // 1). The rewritten constant (c2 - c1, c1 + c2, -c) is computed in the constant's
type, which can overflow or round when the original evaluation on the actual rows would not (or vice versa).
Attack: every integer width (signed/unsigned/hugeint) and decimals, columns holding the type limits, constants near the
limits so c2 - c1 / c1 + c2 / -c overflow, both operand orders, all comparison operators, WHERE and SELECT contexts."""
import random

META = {"name": "control_rule_arith_constants", "author_model": "control:claude-opus-5-5",
        "target": {"optimizer": "expression_rewriter"},
        "description": "x +/-/* constants compared with constants at integer and decimal type limits"}

T = {"TINYINT": (-128, 127), "SMALLINT": (-32768, 32767), "INTEGER": (-2**31, 2**31 - 1), "BIGINT": (-2**63, 2**63 - 1),
     "UTINYINT": (0, 255), "USMALLINT": (0, 65535), "UINTEGER": (0, 2**32 - 1), "UBIGINT": (0, 2**64 - 1), "HUGEINT": (-2**127, 2**127 - 1)}
DEC = {"DECIMAL(4,1)": 999.9, "DECIMAL(9,2)": 9999999.99, "DECIMAL(18,3)": 999999999999999.999}
OPS = ["=", "<>", "<", "<=", ">", ">=", "IS NOT DISTINCT FROM"]


def edge(rng, lo, hi):
    return rng.choice([lo, hi, lo + 1, hi - 1, 0, 1, -1 if lo < 0 else 2, hi // 2, lo // 2, rng.randint(lo, hi), rng.randint(-5, 5) if lo < 0 else rng.randint(0, 5)])


def generate(rng):
    if rng.random() < 0.8:
        t = rng.choice(list(T))
        lo, hi = T[t]
        vals = sorted({edge(rng, lo, hi) for _ in range(rng.randint(1, 8))})
        rows = ", ".join("(CAST(%d AS %s), %d)" % (v, t, i % 3) for i, v in enumerate(vals))
        def c():
            v = edge(rng, lo, hi) if rng.random() < 0.6 else rng.choice(vals) + rng.choice([-1, 0, 1])
            return "CAST(%d AS %s)" % (v, t) if rng.random() < 0.7 else str(v)
    else:
        t = rng.choice(list(DEC))
        m = DEC[t]
        vals = sorted({rng.choice([m, -m, 0, 0.1, -0.1, round(rng.uniform(-m, m), 1)]) for _ in range(rng.randint(1, 6))})
        rows = ", ".join("(CAST('%s' AS %s), %d)" % (v, t, i % 3) for i, v in enumerate(vals))
        def c():
            return "CAST('%s' AS %s)" % (rng.choice([m, -m, 0, 0.5, 1, -1, rng.choice(vals)]), t) if rng.random() < 0.7 else rng.choice(["1", "0", "-1", str(m)])
    setup = ["CREATE TABLE t(x %s, k INTEGER)" % t, "INSERT INTO t VALUES " + rows + (", (NULL, 1)" if rng.random() < 0.3 else ""),
             "CREATE TABLE u(k INTEGER, w INTEGER)", "INSERT INTO u VALUES (0, 1), (1, 2), (2, 3)"]
    qs = []
    for _ in range(rng.randint(3, 6)):
        lhs = rng.choice(["x + %s" % c(), "%s + x" % c(), "x - %s" % c(), "%s - x" % c(), "x * %s" % c(), "-x", "x + 0", "x * 1", "x - 0",
                          "(x + %s) + %s" % (c(), c()), "(x - %s) + %s" % (c(), c()), "%s + (x + %s)" % (c(), c()), "x + %s - %s" % (c(), c()),
                          "x // 1", "x * -1", "0 - x"])
        e = "%s %s %s" % (lhs, rng.choice(OPS), c()) if rng.random() < 0.7 else "%s %s %s" % (c(), rng.choice(OPS), lhs)
        q = rng.choice([
            "SELECT count(*), sum(hash(x)) FROM t WHERE %s",
            "SELECT x, %s AS b FROM t",
            "SELECT k, count(*) FILTER (WHERE %s) FROM t GROUP BY k",
            "SELECT t.x, u.w FROM t JOIN u ON t.k = u.k AND %s",
            "SELECT CASE WHEN %s THEN 1 WHEN x IS NULL THEN 2 ELSE 0 END AS c, count(*) FROM t GROUP BY ALL",
            "SELECT count(*) FROM t WHERE NOT (%s)",
        ])
        qs.append(q % e)
    return {"setup": setup, "queries": qs}
