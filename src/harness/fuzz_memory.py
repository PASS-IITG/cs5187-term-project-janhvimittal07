"""Memory-safety / overflow / null-handling query generator for DuckDB, built to be replayed on the ASan+UBSan build.

    python harness/fuzz_memory.py --n 20000 --seed 1 --out results/memfuzz-queries.jsonl
    python harness/asan_replay.py --input results/memfuzz-queries.jsonl --out results/asan-replay-memfuzz

Why: the earlier function fuzzer calls one function at a time with edge values. Most real memory bugs sit in the
operator and type-crossing code instead: casts between every type pair, arithmetic at the type limits, slicing and
indexing with INT64 extremes, window-frame offsets, nested-type construction and NULL children inside nested values.
Each line of the output is {"id", "sql"}: a self-contained script run with external access disabled. Every family is
emitted both as a constant expression (constant folding path) and over a vector of rows (executor path), because the
two take different code in DuckDB; the 2049-row form crosses the STANDARD_VECTOR_SIZE boundary.

Oracles are the sanitizers (ASan, UBSan with halt_on_error), INTERNAL errors and crashes; asan_replay.py replays every
hit alone before it is believed. Nothing here claims a bug: a UBSan report on INT64_MIN negation is a known upstream class.
"""
from __future__ import annotations

import argparse
import json
import random

INTS = {  # type -> (min, max)
    "TINYINT": (-128, 127), "SMALLINT": (-32768, 32767), "INTEGER": (-2**31, 2**31 - 1),
    "BIGINT": (-2**63, 2**63 - 1), "HUGEINT": (-2**127, 2**127 - 1),
    "UTINYINT": (0, 255), "USMALLINT": (0, 65535), "UINTEGER": (0, 2**32 - 1),
    "UBIGINT": (0, 2**64 - 1), "UHUGEINT": (0, 2**128 - 1),
}
DECS = ["DECIMAL(4,1)", "DECIMAL(9,4)", "DECIMAL(18,3)", "DECIMAL(38,10)", "DECIMAL(38,0)"]
FLOATS = ["FLOAT", "DOUBLE"]
FLOAT_VALS = ["'nan'", "'inf'", "'-inf'", "0", "'-0'", "1e308", "-1e308", "4.9e-324", "3.4e38", "1.17e-38", "9.3e18", "-9.3e18", "1e19"]
STR_VALS = ["''", "'a'", "'é中\U0001f600'", "repeat('ab', 1025)", "repeat('x', 2049)", "'\\x00'::BLOB::VARCHAR",
            "'1e999'", "'-'", "'0x'", "' 12 '", "'9223372036854775808'", "'-9223372036854775809'", "'1.5e'", "'nan'"]
BLOB_VALS = ["''::BLOB", "'\\x00'::BLOB", "'\\xff\\xfe'::BLOB", "repeat('\\xaa', 2049)::BLOB"]
DATE_VALS = ["'infinity'::DATE", "'-infinity'::DATE", "DATE '5881580-07-10'", "DATE '5877642-06-25'", "DATE '4714-11-24 (BC)'",
             "DATE '1970-01-01'", "DATE '0001-01-01'", "DATE '9999-12-31'"]
TS_VALS = ["'infinity'::TIMESTAMP", "'-infinity'::TIMESTAMP", "TIMESTAMP '1677-09-21 00:12:43.145224193'::TIMESTAMP_NS",
           "TIMESTAMP '2262-04-11 23:47:16.854775806'::TIMESTAMP_NS", "TIMESTAMP '294247-01-10 04:00:54.775806'",
           "TIMESTAMP '290309-12-22 (BC) 00:00:00'", "TIMESTAMPTZ '2020-01-01 00:00:00+00'"]
INTERVAL_VALS = ["INTERVAL 0 DAY", "INTERVAL 2147483647 MONTH", "INTERVAL (-2147483648) MONTH", "INTERVAL 2147483647 DAY",
                 "INTERVAL 9223372036854775807 MICROSECOND", "INTERVAL (-9223372036854775808) MICROSECOND", "INTERVAL 1 MONTH 31 DAY 1 HOUR"]
EXT_IDX = ["0", "1", "-1", "2", "-2", "2147483647", "-2147483648", "9223372036854775807", "-9223372036854775807",
           "(-9223372036854775808)::BIGINT", "NULL", "4294967296", "255", "65536"]


def lit_int(t, rng):
    lo, hi = INTS[t]
    v = rng.choice([lo, hi, lo + 1, hi - 1, 0, 1, -1 if lo < 0 else 1, hi // 2, lo // 2, rng.randint(lo, hi)])
    return f"({v})::{t}" if v >= 0 else f"({v})::{t}"


def lit_dec(t, rng):
    p = int(t[8:].split(",")[0])
    s = int(t.split(",")[1][:-1])
    mx = 10 ** p - 1
    v = rng.choice([mx, -mx, 0, 1, -1, mx // 2, rng.randint(-mx, mx)])
    neg = "-" if v < 0 else ""
    digits = str(abs(v)).rjust(s + 1, "0")
    txt = neg + (digits[:-s] + "." + digits[-s:] if s else digits)
    return f"CAST('{txt}' AS {t})"


def values_of(kind, rng):
    if kind in INTS:
        return lambda: lit_int(kind, rng)
    if kind in DECS:
        return lambda: lit_dec(kind, rng)
    if kind in FLOATS:
        return lambda: f"CAST({rng.choice(FLOAT_VALS)} AS {kind})"
    if kind == "VARCHAR":
        return lambda: rng.choice(STR_VALS)
    if kind == "BLOB":
        return lambda: rng.choice(BLOB_VALS)
    if kind == "DATE":
        return lambda: rng.choice(DATE_VALS)
    if kind == "TIMESTAMP":
        return lambda: rng.choice(TS_VALS)
    if kind == "INTERVAL":
        return lambda: rng.choice(INTERVAL_VALS)
    if kind == "BOOLEAN":
        return lambda: rng.choice(["true", "false"])
    raise KeyError(kind)


SRC_KINDS = list(INTS) + DECS + FLOATS + ["VARCHAR", "BLOB", "DATE", "TIMESTAMP", "INTERVAL", "BOOLEAN"]
DST_TYPES = list(INTS) + DECS + FLOATS + ["VARCHAR", "BLOB", "DATE", "TIME", "TIMESTAMP", "TIMESTAMP_NS", "TIMESTAMP_S",
            "TIMESTAMP_MS", "TIMESTAMPTZ", "INTERVAL", "BOOLEAN", "UUID", "BIT", "BIGNUM", "TIME WITH TIME ZONE",
            "INTEGER[]", "VARCHAR[]", "STRUCT(a INTEGER)", "MAP(INTEGER, VARCHAR)", "INTEGER[3]", "ENUM('a','b')"]
NUM = list(INTS) + DECS + FLOATS


def vec(vals):
    """A value list as a vector of rows; padded past STANDARD_VECTOR_SIZE (2048) when asked by the caller."""
    return "(VALUES " + ",".join(f"({v})" for v in vals) + ") t(c)"


def vec2049(v, extra):
    return f"(SELECT CASE WHEN i % 7 = 0 THEN {extra} ELSE {v} END AS c FROM range(2049) r(i)) t"


def fam_cast(rng):
    s = rng.choice(SRC_KINDS)
    d = rng.choice(DST_TYPES)
    g = values_of(s, rng)
    fn = rng.choice(["CAST", "TRY_CAST"])
    form = rng.random()
    if form < 0.3:
        return f"SELECT {fn}({g()} AS {d})"
    if form < 0.7:
        return f"SELECT {fn}(c AS {d}) FROM {vec([g() for _ in range(rng.randint(1, 6))] + ['NULL'])}"
    return f"SELECT {fn}(c AS {d}) FROM {vec2049(g(), 'NULL::' + (s if s not in DECS else s))}"


def fam_arith(rng):
    t = rng.choice(NUM)
    g = values_of(t, rng)
    op = rng.choice(["+", "-", "*", "/", "//", "%", "**", "<<", ">>", "&", "|", "^", "xor"])
    if op in ("<<", ">>", "&", "|", "xor") and (t in FLOATS or t in DECS):
        op = "+"
    mk = lambda a, b: f"xor({a}, {b})" if op == "xor" else f"({a} {op} {b})"
    form = rng.random()
    if form < 0.3:
        return f"SELECT {mk(g(), g())}"
    if form < 0.6:
        return f"SELECT {mk('a', 'b')} FROM (VALUES {','.join(f'({g()},{g()})' for _ in range(rng.randint(1, 6)))}) t(a, b)"
    if form < 0.8:
        return f"SELECT {rng.choice(['-', '~', 'abs', 'sign'])}{'' if rng.random() < 0.5 else ''}(c) FROM {vec([g() for _ in range(4)] + ['NULL'])}" \
            if t not in FLOATS and t not in DECS else f"SELECT abs(c), -c FROM {vec([g() for _ in range(4)])}"
    return f"SELECT {mk('c', 'c')} FROM {vec2049(g(), g())}"


def fam_agg(rng):
    t = rng.choice(list(INTS) + DECS + FLOATS)
    g = values_of(t, rng)
    agg = rng.choice(["sum", "avg", "product", "min", "max", "mode", "median", "stddev_samp", "var_pop", "histogram",
                      "list", "bit_and", "bit_or", "bit_xor", "arg_max", "quantile_cont", "approx_count_distinct",
                      "sum_no_overflow", "kurtosis", "skewness", "first", "any_value"])
    col = "c"
    if agg in ("bit_and", "bit_or", "bit_xor") and (t in FLOATS or t in DECS):
        agg = "sum"
    if agg == "arg_max":
        expr = "arg_max(c, c)"
    elif agg == "quantile_cont":
        expr = f"quantile_cont(c, {rng.choice(['0', '1', '0.5', '1e-300', '0.999999999999'])})"
    elif agg == "sum_no_overflow":
        expr = "sum_no_overflow(c)"
    else:
        expr = f"{agg}({col})"
    extra = rng.choice([g(), "NULL"])
    return f"SELECT {expr} FROM {vec2049(g(), extra)}" if rng.random() < 0.6 else \
        f"SELECT {expr} FROM {vec([g() for _ in range(rng.randint(1, 5))] + ['NULL'])}"


def fam_slice(rng):
    i, j, k = (rng.choice(EXT_IDX) for _ in range(3))
    base = rng.choice(["'hello world'", "repeat('ab', 1025)", "[1,2,3,4,5]", "list_value(NULL, 1, NULL)", "[]::INTEGER[]",
                       "[[1],[2,NULL],NULL]", "['a','b']", "'é中\U0001f600'", "'\\xaa\\xbb'::BLOB",
                       "[1,2,3]::INTEGER[3]", "range(100)", "(SELECT list(i::VARCHAR) FROM range(2049) r(i))"])
    exprs = [f"({base})[{i}:{j}]", f"({base})[{i}:{j}:{k}]", f"({base})[{i}]", f"list_slice({base}, {i}, {j})",
             f"array_slice({base}, {i}, {j}, {k})", f"substring({base}, {i}, {j})", f"left({base}, {i})", f"right({base}, {i})",
             f"lpad({base}::VARCHAR, LEAST({i}::BIGINT, 100000), 'xy')", f"rpad({base}::VARCHAR, LEAST({i}::BIGINT, 100000), '')",
             f"repeat({base}::VARCHAR, LEAST(ABS({i}::HUGEINT), 100000)::BIGINT)",
             f"list_resize({base}, LEAST(ABS({i}::HUGEINT), 100000)::BIGINT)", f"array_extract({base}, {i})",
             f"list_element({base}, {i})", f"substring_grapheme({base}::VARCHAR, {i}, {j})", f"list_select({base}, [{i}])",
             f"list_position({base}, {i})", f"string_split({base}::VARCHAR, '')[{i}]", f"bit_position('101'::BIT, '10101'::BIT)",
             f"get_bit('1010'::BIT, {i})", f"set_bit('1010'::BIT, {i}, 1)", f"bitstring('1', LEAST(ABS({i}::HUGEINT), 100000)::INTEGER)"]
    e = rng.choice(exprs)
    return f"SELECT {e}"


def fam_frame(rng):
    a, b = rng.choice(EXT_IDX[:-1]), rng.choice(EXT_IDX[:-1])
    unit = rng.choice(["ROWS", "ROWS", "RANGE", "GROUPS"])
    fn = rng.choice(["sum(x)", "count(*)", "min(x)", "first_value(x)", "last_value(x)", "nth_value(x, 2)", "avg(x)",
                     "lead(x, 2147483647)", f"lead(x, {a})", f"lag(x, {a}, 0)", "ntile(3)", "percent_rank()", "list(x)",
                     "median(x)", "mode(x)", "arg_min(x, y)"])
    if unit == "RANGE":
        a, b = rng.choice(["0", "1", "9223372036854775807", "2147483647", "NULL"]), rng.choice(["0", "1", "9223372036854775807", "NULL"])
    lo = rng.choice(["UNBOUNDED", f"{a}"])
    hi = rng.choice(["UNBOUNDED", f"{b}"])
    frame = f"{unit} BETWEEN {lo} PRECEDING AND {hi} FOLLOWING" if rng.random() < 0.8 else f"{unit} BETWEEN {lo} FOLLOWING AND {hi} FOLLOWING"
    ty = rng.choice(["BIGINT", "INTEGER", "DOUBLE", "DECIMAL(18,3)", "HUGEINT"])
    g = values_of(ty if ty != "BIGINT" else "BIGINT", rng)
    rows = ",".join(f"({g()},{rng.randint(0, 3)})" for _ in range(rng.randint(3, 10)))
    n = rng.choice([1, 1, 3000])
    src = f"(VALUES {rows}) t(x, y)" if n == 1 else f"(SELECT (i * 7919) % 5 AS x, i % 3 AS y FROM range(3000) r(i)) t"
    return f"SELECT {fn} OVER (PARTITION BY y ORDER BY x {frame}) FROM {src}"


def fam_range(rng):
    a, b, c = (rng.choice(EXT_IDX[:-1] + ["100"]) for _ in range(3))
    kind = rng.choice(["range", "generate_series", "limit", "offset", "sample", "unnest", "series_ts"])
    if kind in ("range", "generate_series"):
        return f"SELECT count(*) FROM (SELECT * FROM {kind}(({a})::BIGINT, LEAST(({b})::HUGEINT, ({a})::HUGEINT + 100000)::BIGINT, ({c})::BIGINT)) q"
    if kind == "limit":
        return f"SELECT * FROM range(100) LIMIT {a}"
    if kind == "offset":
        return f"SELECT * FROM range(100) LIMIT 5 OFFSET {a}"
    if kind == "sample":
        return f"SELECT count(*) FROM range(3000) USING SAMPLE {rng.choice(['0', '1', '100', '99.99999', '9223372036854775807', '3000'])} {rng.choice(['ROWS', '%'])}"
    if kind == "unnest":
        return f"SELECT unnest(range(LEAST(ABS(({a})::HUGEINT), 5000)::BIGINT))"
    return f"SELECT count(*) FROM generate_series(TIMESTAMP '2020-01-01', TIMESTAMP '2020-01-02', INTERVAL {rng.choice(['1 HOUR', '1 MICROSECOND', '0 DAY'])}) q"


def fam_nested(rng):
    d = rng.choice([1, 2, 5, 20, 60, 200])
    inner = rng.choice(["1", "NULL", "'x'", "NULL::INTEGER", "[]::INTEGER[]"])
    kind = rng.choice(["list", "struct", "map", "union"])
    e = inner
    for _ in range(d):
        e = {"list": f"[{e}, NULL]", "struct": f"{{'a': {e}, 'b': NULL}}", "map": f"MAP {{1: {e}}}",
             "union": f"union_value(k := {e})"}[kind]
    ops = [f"SELECT {e}", f"SELECT {e}::VARCHAR", f"SELECT to_json({e})", f"SELECT {e} = {e}", f"SELECT {e} < {e}",
           f"SELECT hash({e})", f"SELECT count(DISTINCT c) FROM (SELECT {e} AS c UNION ALL SELECT NULL UNION ALL SELECT {e}) q",
           f"SELECT c FROM (SELECT {e} AS c) ORDER BY c", f"SELECT list_concat([{e}], [{e}, NULL])",
           f"SELECT unnest([{e}, NULL])", f"SELECT min(c), max(c) FROM (VALUES ({e}), (NULL)) t(c)",
           f"SELECT map_from_entries([({rng.choice(['1', 'NULL', '1'])}, {e}), (1, NULL)])" if kind != "map" else f"SELECT map_keys({e})",
           f"SELECT struct_pack(a := {e}, b := {e}).a", f"SELECT {e} IS NOT DISTINCT FROM NULL"]
    return rng.choice(ops)


def fam_datetime(rng):
    d = rng.choice(DATE_VALS)
    ts = rng.choice(TS_VALS)
    iv = rng.choice(INTERVAL_VALS)
    iv2 = rng.choice(INTERVAL_VALS)
    big = rng.choice(["9223372036854775807", "-9223372036854775808", "4294967296", "253402300800", "-62135596801", "1e300", "'nan'", "'inf'"])
    exprs = [f"SELECT {d} + {iv}", f"SELECT {d} - {iv}", f"SELECT {ts} + {iv}", f"SELECT {ts} - {iv}", f"SELECT {iv} + {iv2}",
             f"SELECT {iv} * 1000000", f"SELECT {iv} / 3", f"SELECT {iv} * 9223372036854775807::DOUBLE",
             f"SELECT date_diff('microsecond', {d}, {ts})", f"SELECT datediff('{rng.choice(['year', 'month', 'day', 'ms'])}', {d}, {ts})",
             f"SELECT date_trunc('{rng.choice(['year', 'century', 'millennium', 'week', 'microsecond'])}', {ts})",
             f"SELECT date_part('{rng.choice(['epoch', 'isoyear', 'julian', 'era', 'nanosecond'])}', {ts})",
             f"SELECT to_timestamp(({big})::DOUBLE)", f"SELECT epoch_ms(({big})::BIGINT)", f"SELECT make_timestamp(({big})::BIGINT)",
             f"SELECT make_timestamp_ns(({big})::BIGINT)", f"SELECT make_date({rng.choice(EXT_IDX[:-1])}::INTEGER, 1, 1)",
             f"SELECT strftime({ts}, '%{rng.choice('aAbBcdfGHIjmMpSUwWxXyYzZ%')}')", f"SELECT age({ts}, {ts})",
             f"SELECT time_bucket({iv}, {ts})", f"SELECT time_bucket(INTERVAL 1 DAY, {ts}, {d})", f"SELECT {ts}::TIME", f"SELECT {ts}::DATE",
             f"SELECT epoch({ts})", f"SELECT {ts} AT TIME ZONE 'America/New_York'", f"SELECT last_day({d})", f"SELECT {d} + {rng.choice(EXT_IDX[:-1])}::INTEGER"]
    return rng.choice(exprs)


def fam_null_nested(rng):
    """NULL children in every nested type, then run common operations on them (null-pointer / validity-mask paths)."""
    ty = rng.choice(["INTEGER[]", "VARCHAR[]", "STRUCT(a INTEGER, b VARCHAR)", "MAP(VARCHAR, INTEGER)", "INTEGER[2]", "STRUCT(a INTEGER[])[]",
                     "UNION(i INTEGER, s VARCHAR)", "VARCHAR[][]"])
    vals = {"INTEGER[]": ["NULL", "[]", "[NULL]", "[1,NULL,3]"], "VARCHAR[]": ["NULL", "[NULL]", "['a',NULL]", "[repeat('x',100)]"],
            "STRUCT(a INTEGER, b VARCHAR)": ["NULL", "{'a':NULL,'b':NULL}", "{'a':1,'b':'x'}"],
            "MAP(VARCHAR, INTEGER)": ["NULL", "MAP {}", "MAP {'a': NULL}", "MAP {'a': 1}"],
            "INTEGER[2]": ["NULL", "[NULL, NULL]", "[1, NULL]"],
            "STRUCT(a INTEGER[])[]": ["NULL", "[NULL]", "[{'a': NULL}]", "[{'a': [NULL]}]"],
            "UNION(i INTEGER, s VARCHAR)": ["NULL", "union_value(i := NULL)", "union_value(s := 'x')"],
            "VARCHAR[][]": ["NULL", "[NULL]", "[[NULL]]", "[[], NULL]"]}[ty]
    rows = ",".join(f"({rng.choice(vals)}::{ty})" for _ in range(rng.randint(1, 8)))
    op = rng.choice(["c", "c::VARCHAR", "hash(c)", "c = c", "c < c", "typeof(c)", "to_json(c)", "count(c)", "min(c)", "max(c)", "list(c)",
                     "first(c)", "mode(c)", "histogram(c)", "count(DISTINCT c)", "arg_max(c, c)", "unnest(c)" if "[" in ty else "c IS NULL",
                     "length(c)" if "[]" in ty else "c IS NOT NULL", "array_to_string(c, ',')" if ty.startswith("VARCHAR[]") else "c IS NULL",
                     "c ORDER BY c", "map_keys(c)" if ty.startswith("MAP") else "c IS NULL"])
    if " ORDER BY" in op:
        return f"SELECT c FROM (VALUES {rows}) t(c) ORDER BY c"
    if rng.random() < 0.3:
        return f"SELECT {op} FROM (VALUES {rows}) t(c) UNION ALL SELECT {op} FROM (VALUES {rows}) t(c)" if "(" not in op or "unnest" in op else \
            f"SELECT {op} FROM (VALUES {rows}) t(c)"
    return f"SELECT {op} FROM (VALUES {rows}) t(c)"


FAMILIES = [(fam_cast, 22), (fam_arith, 18), (fam_agg, 10), (fam_slice, 14), (fam_frame, 10), (fam_range, 5),
            (fam_nested, 7), (fam_datetime, 8), (fam_null_nested, 6)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    fns = [f for f, w in FAMILIES for _ in range(w)]
    seen = set()
    with open(a.out, "w") as fh:
        k = 0
        while k < a.n:
            fam = rng.choice(fns)
            try:
                q = fam(rng)
            except Exception:
                continue
            if q in seen:
                continue
            seen.add(q)
            # plain, and verification mode (DuckDB's own consistency checks: vector round trips, serialization, etc.)
            for tag, pre in (("plain", ""), ("verify", "PRAGMA enable_verification;\n")):
                sql = f"SET enable_external_access=false;\n{pre}{q};\n"
                fh.write(json.dumps({"id": f"mem{a.seed}:{k}:{fam.__name__[4:]}:{tag}", "sql": sql}) + "\n")
            k += 1
    print(f"wrote {a.n} queries x2 (plain + verify) to {a.out}")


if __name__ == "__main__":
    main()
