"""REDISCOVERY CONTROL for upstream 28532 (not LLM-loop output; never copy into $FUZZ_WORK/generators).
CAST(col AS T) OP constant, rewritten by ComparisonSimplificationRule into col OP CAST(constant AS coltype) when the
cast is judged invertible (integral widening, round-tripping narrowing decimals, floating identities, DATE vs TIMESTAMP
day bumps). Attack: every pair of column/target types, constants taken from the data (so = can match) and from the edges
of both types (min/max, +-0.5, NaN, +-inf, -0.0, precision limits of FLOAT/DOUBLE, midnight vs non-midnight)."""
import random

META = {"name": "control_rule_cast_comparison", "author_model": "control:claude-opus-5-5",
        "target": {"optimizer": "expression_rewriter", "rule": "comparison_simplification", "marker": ["cast"]},
        "description": "CAST(col AS T) OP const across numeric/decimal/float/temporal/varchar type pairs"}

INTS = {"TINYINT": (-128, 127), "SMALLINT": (-32768, 32767), "INTEGER": (-2**31, 2**31 - 1), "BIGINT": (-2**63, 2**63 - 1),
        "UTINYINT": (0, 255), "USMALLINT": (0, 65535), "UINTEGER": (0, 2**32 - 1), "UBIGINT": (0, 2**64 - 1),
        "HUGEINT": (-2**127, 2**127 - 1)}
DECS = ["DECIMAL(4,1)", "DECIMAL(5,2)", "DECIMAL(9,3)", "DECIMAL(18,0)", "DECIMAL(18,6)", "DECIMAL(38,10)", "DECIMAL(3,3)", "DECIMAL(2,0)"]
FLTS = ["FLOAT", "DOUBLE"]
TEMP = ["DATE", "TIMESTAMP", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS", "TIMESTAMPTZ"]
NUMERIC = list(INTS) + DECS + FLTS


def num_val(rng, t):
    if t in INTS:
        lo, hi = INTS[t]
        return str(rng.choice([lo, hi, 0, 1, -1 if lo < 0 else 2, lo + 1, hi - 1, rng.randint(-200, 200) if lo < 0 else rng.randint(0, 300),
                               2**24 + 1, 2**53 + 1, rng.randint(lo, hi)]))
    if t in FLTS:
        return rng.choice(["0.1", "1.5", "2.5", "-2.5", "-0.0", "0.0", "16777217", "9007199254740993", "1e38", "3.4e38", "1e308",
                           "'nan'", "'inf'", "'-inf'", "%d.%d" % (rng.randint(-99, 99), rng.randint(0, 999)), "1e-45", "2147483647.5"])
    p, s = [int(x) for x in t[8:-1].split(",")]
    whole = "9" * (p - s) if p > s else "0"
    return rng.choice([whole + ("." + "9" * s if s else ""), "-" + whole, "0", "1.25" if s >= 2 else "1", "0.5" if s >= 1 else "0",
                       "%d.%0*d" % (rng.randint(0, 9), max(s, 1), rng.randint(0, 10 ** max(s, 1) - 1)), "-0.05" if s >= 2 else "-1"])


def temp_val(rng, t):
    d = "%04d-%02d-%02d" % (rng.choice([1, 1970, 2000, 2024, 9999, rng.randint(1, 3000)]), rng.randint(1, 12), rng.choice([1, 15, 28, rng.randint(1, 28)]))
    if t != "DATE" and rng.random() < 0.7:
        d += rng.choice([" 00:00:00", " 12:00:00", " 23:59:59.999999", " 00:00:00.000001", " %02d:%02d:%02d.%03d" % (rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59), rng.randint(0, 999))])
    return "'%s'" % rng.choice([d, d, d, "infinity", "-infinity"])


def val(rng, t):
    return temp_val(rng, t) if t in TEMP else num_val(rng, t) if t in NUMERIC else "'%s'" % rng.choice(["1", "01", " 1", "1.0", "abc", "", "2024-01-01", "-0"])


ORDER = ["TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT"]
UORDER = ["UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "HUGEINT"]


def pair(rng):
    """Mostly casts the rule may treat as invertible (widening, decimal scale-up, DATE -> TIMESTAMP*), plus near-misses."""
    k = rng.random()
    if k < 0.25:
        o = rng.choice([ORDER, UORDER])
        i = rng.randrange(len(o) - 1)
        return o[i], rng.choice(o[i + 1:] + ["DOUBLE", "DECIMAL(38,10)", "DECIMAL(18,0)"])
    if k < 0.45:
        a, b = rng.sample(DECS, 2)
        return a, rng.choice([b, "DOUBLE", "FLOAT", "HUGEINT", "BIGINT"])
    if k < 0.6:
        return rng.choice(["FLOAT", "DOUBLE", "INTEGER", "BIGINT"]), rng.choice(["DOUBLE", "FLOAT", "DECIMAL(18,6)"])
    if k < 0.85:
        return rng.choice(TEMP), rng.choice(TEMP)
    return rng.choice(NUMERIC), rng.choice(NUMERIC)


OPS = ["=", "<>", "<", "<=", ">", ">=", "IS DISTINCT FROM", "IS NOT DISTINCT FROM"]


def generate(rng):
    ct, tt = pair(rng)
    data = [val(rng, ct) for _ in range(rng.randint(1, 20))]
    rows = ", ".join("(TRY_CAST(%s AS %s), %d)" % (v, ct, i % 3) for i, v in enumerate(data)) + ", (NULL, 1)" * rng.randint(0, 1)
    setup = ["CREATE TABLE t(x %s, k INTEGER)" % ct, "INSERT INTO t VALUES " + rows,
             "CREATE TABLE u(k INTEGER, w INTEGER)", "INSERT INTO u VALUES (0, 1), (1, 2), (2, 3), (NULL, 4)"]
    qs = []
    for _ in range(rng.randint(3, 6)):
        r = rng.random()
        if r < 0.5:      # a data value seen through the target type: equality can match, ordering sits on a boundary
            c = "TRY_CAST(TRY_CAST(%s AS %s) AS %s)" % (rng.choice(data), ct, tt)
        elif r < 0.85:
            c = "CAST(%s AS %s)" % (val(rng, tt), tt)
        else:
            c = "(TRY_CAST(%s AS %s) %s 1)" % (rng.choice(data), tt, rng.choice("+-")) if tt in NUMERIC else "TRY_CAST(%s AS %s)" % (val(rng, tt), tt)
        cast = rng.choice(["CAST(x AS %s)" % tt, "x::%s" % tt])
        op = rng.choice(OPS)
        e = "%s %s %s" % (cast, op, c) if rng.random() < 0.6 else "%s %s %s" % (c, op, cast)
        q = rng.choice([
            "SELECT count(*), sum(hash(x)) FROM t WHERE %s",
            "SELECT x, %s AS b FROM t",
            "SELECT k, count(*) FILTER (WHERE %s) FROM t GROUP BY k",
            "SELECT t.x, u.w FROM t JOIN u ON t.k = u.k AND %s",
            "SELECT CASE WHEN %s THEN 1 WHEN x IS NULL THEN 2 ELSE 0 END AS c, count(*) FROM t GROUP BY ALL",
            "SELECT u.w, (SELECT count(*) FROM t WHERE t.k = u.k AND %s) FROM u",
            "SELECT count(*) FROM t WHERE NOT (%s)",
        ])
        qs.append(q % e)
    return {"setup": setup, "queries": qs}
