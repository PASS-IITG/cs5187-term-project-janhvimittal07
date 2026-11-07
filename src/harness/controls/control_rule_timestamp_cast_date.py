"""REDISCOVERY CONTROL for upstream 28531 (not LLM-loop output; never copy into $FUZZ_WORK/generators).
CAST(<timestamp col> AS DATE) = <DATE constant>, rewritten by TimeStampComparison into
ts >= TIMESTAMP(d) AND ts < TIMESTAMP(d + 1). Attack surface: DATE has a far wider domain than TIMESTAMP (Value::TIMESTAMP is
built from the date without a range check), infinities, the day before/after the TIMESTAMP limits, BC dates, constants that
are only foldable (CAST of a string, date arithmetic), and near-miss column types the rule must leave alone."""
import random

META = {"name": "control_rule_timestamp_cast_date", "author_model": "control:claude-opus-5-5",
        "target": {"optimizer": "expression_rewriter", "rule": "timestamp_comparison", "marker": ["cast"]},
        "description": "CAST(ts AS DATE) = DATE constant (TimeStampComparison), TIMESTAMP/DATE domain edges"}

TS_EDGE = ["294247-01-10 04:00:54.775806", "294247-01-10 00:00:00", "294247-01-09 23:59:59", "290309-12-22 (BC) 00:00:00",
           "290309-12-21 (BC) 23:59:59", "infinity", "-infinity", "1970-01-01 00:00:00", "0001-01-01 00:00:00",
           "0001-12-31 (BC) 23:59:59.999999", "2000-02-29 12:00:00"]
DATE_EDGE = ["294247-01-10", "294247-01-11", "294248-01-01", "1000000-01-01", "5881580-07-10", "5881580-07-09",
             "290309-12-22 (BC)", "290309-12-21 (BC)", "290310-01-01 (BC)", "5877642-06-25 (BC)", "infinity", "-infinity",
             "0001-01-01", "0001-12-31 (BC)", "1970-01-01"]
COLTYPES = ["TIMESTAMP"] * 6 + ["TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS", "TIMESTAMPTZ", "DATE"]


def ts(rng):
    if rng.random() < 0.35:
        return rng.choice(TS_EDGE)
    return "%04d-%02d-%02d %02d:%02d:%02d" % (rng.choice([rng.randint(1, 9999), rng.randint(1960, 2030)]), rng.randint(1, 12),
                                              rng.randint(1, 28), rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59))


def date_const(rng, data):
    r = rng.random()
    if r < 0.45 and data:
        d = rng.choice(data)
        d = d.split(" ")[0] + (" (BC)" if "(BC)" in d else "")         # the date part of a row: = can match
        if d in ("infinity", "-infinity") or rng.random() < 0.7:
            return "DATE '%s'" % d
        return "(DATE '%s' %s INTERVAL %d DAY)::DATE" % (d, rng.choice("+-"), rng.randint(1, 2))
    if r < 0.85:
        return "DATE '%s'" % rng.choice(DATE_EDGE)
    return rng.choice(["CAST('%s' AS DATE)" % rng.choice(DATE_EDGE), "DATE '1970-01-01' + %d" % rng.randint(-3, 3)])


def generate(rng):
    ct = rng.choice(COLTYPES)
    data = [ts(rng) for _ in range(rng.randint(1, 25))]
    rows = ["CAST('%s' AS %s)" % (d, ct) for d in data] + ["NULL"] * rng.randint(0, 2)
    setup = ["CREATE TABLE t(x %s, k INTEGER)" % ct,
             "INSERT INTO t VALUES " + ", ".join("(TRY_CAST(%s AS %s), %d)" % (r, ct, i % 3) for i, r in enumerate(rows)),
             "CREATE TABLE u(k INTEGER, w INTEGER)", "INSERT INTO u VALUES (0, 1), (1, 2), (2, 3), (NULL, 4)"]
    qs = []
    for _ in range(rng.randint(3, 6)):
        cast = rng.choice(["CAST(x AS DATE)", "x::DATE", "CAST(t.x AS DATE)"])
        c = date_const(rng, data)
        e = "%s = %s" % (cast, c) if rng.random() < 0.6 else "%s = %s" % (c, cast)
        if rng.random() < 0.15:
            e = "NOT (%s)" % e
        q = rng.choice([
            "SELECT count(*), sum(hash(x)) FROM t WHERE %s",
            "SELECT x, %s AS b FROM t",
            "SELECT k, count(*) FILTER (WHERE %s) FROM t GROUP BY k",
            "SELECT k, count(*) FROM t GROUP BY k HAVING bool_or(%s)",
            "SELECT t.x, u.w FROM t JOIN u ON t.k = u.k AND %s",
            "SELECT CASE WHEN %s THEN 1 WHEN x IS NULL THEN 2 ELSE 0 END AS c, count(*) FROM t GROUP BY ALL",
            "SELECT u.w, (SELECT count(*) FROM t WHERE t.k = u.k AND %s) FROM u",
            "SELECT count(*) FROM t WHERE %s OR k = 7",
        ])
        qs.append(q % e)
    return {"setup": setup, "queries": qs}
