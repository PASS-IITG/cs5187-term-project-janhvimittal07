"""REDISCOVERY CONTROL for upstream 28534 (not LLM-loop output; never copy into $FUZZ_WORK/generators).
date_trunc(part, col) OP constant near the edges of the temporal domains. DateTruncSimplificationRule builds
x >= date_trunc(part, c) AND x < date_trunc(part, c + INTERVAL 1 part) and casts the bounds to the column type. Near the
largest/smallest TIMESTAMP (294247 / 290309 BC), DATE (5881580 / 5877642 BC), TIMESTAMP_NS (1677 / 2262), around year 0/1
(BC/AD), and for week/isoyear/quarter/decade/century/millennium buckets, c + 1 part or its truncation may overflow,
change era, or land in a different bucket. Constants are the rows' own truncations (so = matches) and the domain edges.
Day-and-coarser parts on DATE and all parts on TIMESTAMP-like columns (sub-day on DATE is issue 28527, excluded here)."""
import random

META = {"name": "control_rule_date_trunc_edges", "author_model": "control:claude-opus-5-5",
        "target": {"optimizer": "expression_rewriter", "rule": "date_trunc_simplification", "marker": ["date_trunc", "datetrunc"]},
        "description": "date_trunc(part, col) OP const at the TIMESTAMP/DATE/TIMESTAMP_NS domain edges and the BC/AD boundary"}

COARSE = ["millennium", "century", "decade", "year", "quarter", "month", "week", "isoyear", "day"]
FINE = ["hour", "minute", "second", "milliseconds", "microseconds"]
EDGES = {
    "TIMESTAMP": ["294247-01-10 04:00:54.775806", "294247-01-10 00:00:00", "294247-01-01 00:00:00", "294246-12-31 23:00:00", "294240-06-15 12:00:00",
                  "290309-12-22 (BC) 00:00:00", "290309-12-31 (BC) 23:59:59", "290300-01-01 (BC) 00:00:00", "290309-12-25 (BC) 10:00:00",
                  "0001-01-01 00:00:00", "0001-12-31 (BC) 23:59:59", "0001-06-15 (BC) 12:00:00", "0010-01-01 00:00:00", "0100-12-31 23:59:59", "1000-01-01 (BC) 00:00:00"],
    "TIMESTAMP_NS": ["2262-04-11 23:47:16.854775", "2262-04-11 00:00:00", "2262-01-01 00:00:00", "1677-09-21 00:12:43.145225", "1677-09-22 00:00:00",
                     "1677-12-31 23:59:59", "1678-01-01 00:00:00", "2200-01-01 00:00:00", "1700-01-01 00:00:00"],
    "DATE": ["5881580-07-10", "5881580-07-01", "5881580-01-01", "5881579-12-31", "5881500-01-01", "5877642-06-25 (BC)", "5877642-12-31 (BC)",
             "5877641-01-01 (BC)", "5877600-01-01 (BC)", "0001-01-01", "0001-12-31 (BC)", "0002-01-01 (BC)", "0001-06-15 (BC)", "0100-01-01", "1000-01-01 (BC)"],
}
EDGES["TIMESTAMP_S"] = [e.split(".")[0] for e in EDGES["TIMESTAMP"]] + ["294247-01-10 04:00:54"]
EDGES["TIMESTAMP_MS"] = [e[:-3] if "." in e else e for e in EDGES["TIMESTAMP"]]
EDGES["TIMESTAMPTZ"] = [e + "+00" if "BC" not in e else e.replace(" (BC)", "") + " (BC)" for e in EDGES["TIMESTAMP"]]
OPS = ["=", "<>", "<", "<=", ">", ">=", "IS NOT DISTINCT FROM", "IS DISTINCT FROM"]


def generate(rng):
    t = rng.choice(list(EDGES) + ["TIMESTAMP", "DATE"])
    edges = EDGES[t]
    vals = rng.sample(edges, rng.randint(1, min(8, len(edges))))
    rows = ", ".join("(TRY_CAST('%s' AS %s), %d)" % (v, t, i % 3) for i, v in enumerate(vals)) + (", (NULL, 1)" if rng.random() < 0.3 else "")
    setup = ["SET TimeZone = 'UTC'", "CREATE TABLE t(x %s, k INTEGER)" % t, "INSERT INTO t VALUES " + rows,
             "CREATE TABLE u(k INTEGER, w INTEGER)", "INSERT INTO u VALUES (0, 1), (1, 2), (2, 3)"]
    qs = []
    parts = COARSE if t == "DATE" else COARSE + FINE
    for _ in range(rng.randint(3, 6)):
        p = rng.choice(parts)
        r = rng.random()
        ct = rng.choice([t, t, "TIMESTAMP", "DATE"])
        if r < 0.5:      # a row's own truncation: = must match that row
            c = "(SELECT date_trunc('%s', TRY_CAST('%s' AS %s)))" % (p, rng.choice(vals), t)
        elif r < 0.85:
            c = "TRY_CAST('%s' AS %s)" % (rng.choice(edges), ct)
        else:
            c = "TRY_CAST('%s' AS %s)" % (rng.choice(EDGES[rng.choice(list(EDGES))]), ct)
        if r < 0.5 and rng.random() < 0.5:
            c = c[len("(SELECT "):-1]      # the same, as a foldable constant expression instead of a subquery
        e = "date_trunc('%s', x) %s %s" % (p, rng.choice(OPS), c) if rng.random() < 0.6 else "%s %s date_trunc('%s', x)" % (c, rng.choice(OPS), p)
        q = rng.choice([
            "SELECT count(*), sum(hash(x)) FROM t WHERE %s",
            "SELECT x::VARCHAR, %s AS b FROM t",
            "SELECT k, count(*) FILTER (WHERE %s) FROM t GROUP BY k",
            "SELECT t.k, u.w FROM t JOIN u ON t.k = u.k AND %s",
            "SELECT CASE WHEN %s THEN 1 WHEN x IS NULL THEN 2 ELSE 0 END AS c, count(*) FROM t GROUP BY ALL",
            "SELECT count(*) FROM t WHERE NOT (%s)",
        ])
        qs.append(q % e)
    return {"setup": setup, "queries": qs}
