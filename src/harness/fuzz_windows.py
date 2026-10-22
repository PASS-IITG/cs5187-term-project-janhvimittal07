"""Window-function fuzzer: LAG/LEAD defaults of other types, extreme offsets, odd frames, mixed column types.

    python harness/fuzz_windows.py --n 20000 --seed 1                 # run on the 2.0.0 wheel (plain, then enable_verification)
    python harness/fuzz_windows.py --n 20000 --emit results/win.jsonl # self-contained scripts for asan_replay.py

Motivation (SmartFuzz, OOPSLA 2026): DuckDB bugs #20615 (LAG(varchar, 10, 3) OVER () - default of a mismatched type,
wild pointer in string copy) and #20481 (LEAD(1) OVER () + COALESCE - UNKNOWN type reaching physical planning) were both
window functions with a default/argument whose type does not match the column. Our function fuzzer skips window
functions entirely, so this covers them. Oracles: INTERNAL / FATAL error, worker crash, optimizer on/off result diff.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bottomup  # noqa: E402
import devcheck  # noqa: E402

WORK = bottomup.WORK
OUT = WORK / "results" / "windows"

SETUP = [
    "CREATE TABLE w(k INT, i INT, j INT, s VARCHAR, d DATE, dc DECIMAL(18,3), bl BLOB, l INT[], st STRUCT(x INT, y VARCHAR), "
    "ts TIMESTAMP, h HUGEINT, f DOUBLE, u UUID, b BOOLEAN, big BIGINT, tiny TINYINT)",
    "INSERT INTO w VALUES "
    "(1, 1, 2, 'a', DATE '2020-01-01', 1.5, 'x'::BLOB, [1,2], {'x':1,'y':'p'}, TIMESTAMP '2020-01-01 00:00:00', 1::HUGEINT, 0.5, "
    "'00000000-0000-0000-0000-000000000001', true, 9223372036854775807, 127), "
    "(2, 2, NULL, 'b', DATE '1999-12-31', -2.25, ''::BLOB, [], {'x':NULL,'y':'q'}, TIMESTAMP '1999-12-31 23:59:59', -1::HUGEINT, -0.0, "
    "'ffffffff-ffff-ffff-ffff-ffffffffffff', false, -9223372036854775808, -128), "
    "(3, NULL, 3, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'nan'::DOUBLE, NULL, NULL, NULL, NULL), "
    "(4, 2, 3, 'a', DATE '2020-01-01', 1.5, 'x'::BLOB, [2,NULL], {'x':2,'y':NULL}, TIMESTAMP '2020-01-01 00:00:00', 0::HUGEINT, 1e308, "
    "'00000000-0000-0000-0000-000000000001', NULL, 0, 0), "
    "(5, 2147483647, -2147483648, repeat('z', 3000), DATE '9999-12-31', 9999999999999.999, '\\x00\\xff'::BLOB, [1,1,1], {'x':1,'y':'p'}, "
    "TIMESTAMP '9999-12-31 23:59:59', 170141183460469231731687303715884105727::HUGEINT, 'inf'::DOUBLE, NULL, true, 1, 1)",
]
COLS = ["i", "j", "s", "d", "dc", "bl", "l", "st", "ts", "h", "f", "u", "b", "big", "tiny"]
SCALARS = ["i", "j", "s", "d", "dc", "bl", "h", "f", "u", "b", "big", "tiny", "ts"]
ORDERABLE = ["i", "j", "s", "d", "dc", "f", "ts", "big", "h", "l", "NULL", "i DESC NULLS FIRST", "s DESC", "f DESC NULLS LAST"]
PARTS = ["", "PARTITION BY b", "PARTITION BY s", "PARTITION BY i", "PARTITION BY l", "PARTITION BY st", "PARTITION BY d, b"]
OFFSETS = ["0", "1", "-1", "2", "10", "NULL", "j", "i + 1", "9223372036854775807", "-9223372036854775808", "2147483648",
           "2147483647", "tiny", "big"]
DEFAULTS = ["NULL", "3", "'x'", "[1]", "{'x': 1}", "DATE '2020-01-01'", "1.5", "''::BLOB", "true", "s", "i", "l", "st", "d",
            "9223372036854775807", "repeat('q', 3000)", "'00000000-0000-0000-0000-000000000000'::UUID", "1::HUGEINT"]
FRAMES = ["", "ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING", "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW",
          "ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING", "ROWS BETWEEN j PRECEDING AND i FOLLOWING",
          "ROWS BETWEEN 9223372036854775807 PRECEDING AND CURRENT ROW", "ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING",
          "RANGE BETWEEN 1 PRECEDING AND 1 FOLLOWING", "RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW",
          "GROUPS BETWEEN 1 PRECEDING AND 1 FOLLOWING", "ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING EXCLUDE CURRENT ROW",
          "ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING EXCLUDE TIES",
          "GROUPS BETWEEN CURRENT ROW AND 1 FOLLOWING EXCLUDE GROUP"]
AGGS = ["sum", "min", "max", "count", "avg", "any_value", "first", "last", "list", "string_agg", "median", "mode",
        "arg_min", "arg_max", "bool_and", "histogram", "approx_count_distinct", "product", "stddev_samp", "mad"]


def col(rng: random.Random) -> str:
    return rng.choice(COLS)


def window_expr(rng: random.Random) -> str:
    kind = rng.choice(["lag", "lead", "lag", "lead", "value", "nth", "rank", "ntile", "agg", "agg", "fill"])
    c = col(rng)
    nulls = rng.choice(["", "", "IGNORE NULLS", "RESPECT NULLS"])
    if kind in ("lag", "lead"):
        args = [c]
        if rng.random() < 0.9:
            args.append(rng.choice(OFFSETS))
            if rng.random() < 0.8:
                args.append(rng.choice(DEFAULTS))
        fn = f"{kind}({', '.join(args)} {nulls})" if nulls else f"{kind}({', '.join(args)})"
        if rng.random() < 0.2:                      # SmartFuzz #20481: window function inside COALESCE
            fn = f"coalesce({fn}, {rng.choice(DEFAULTS)})"
    elif kind == "value":
        fn = f"{rng.choice(['first_value', 'last_value'])}({c} {nulls})".replace("  ", " ")
    elif kind == "nth":
        fn = f"nth_value({c}, {rng.choice(['1', '2', '0', '-1', 'NULL', 'j', '9223372036854775807'])} {nulls})".replace("  ", " ")
    elif kind == "rank":
        fn = rng.choice(["rank()", "dense_rank()", "percent_rank()", "cume_dist()", "row_number()"])
    elif kind == "ntile":
        fn = f"ntile({rng.choice(['1', '2', '0', '-1', 'NULL', 'j', '2147483647', '9223372036854775807'])})"
    elif kind == "fill":
        fn = f"fill({rng.choice(['i', 'f', 'd', 'dc', 'ts', 'big'])})"
    else:
        a = rng.choice(AGGS)
        if a in ("arg_min", "arg_max"):
            fn = f"{a}({col(rng)}, {col(rng)})"
        elif a == "string_agg":
            fn = f"string_agg({rng.choice(['s', 'i', 'l::VARCHAR'])}, {rng.choice([chr(39) + ',' + chr(39), 's'])})"
        else:
            fn = f"{a}({c})"
    return fn


def build(rng: random.Random) -> str:
    parts = []
    nodiff = False                      # windows whose result may legitimately differ between plans (peers / no order)
    for _ in range(rng.choice([1, 1, 1, 2, 3])):
        order = rng.choice(ORDERABLE) if rng.random() < 0.8 else ""
        frame = rng.choice(FRAMES) if rng.random() < 0.5 else ""
        if not order or "RANGE" in frame or "GROUPS" in frame:
            nodiff = True               # RANGE/GROUPS offsets need a single key, so ties stay
        else:
            order += ", k"              # unique tie-breaker: makes the ordering total
        spec = " ".join(x for x in (rng.choice(PARTS), ("ORDER BY " + order) if order else "", frame) if x)
        parts.append(f"{window_expr(rng)} OVER ({spec})")
    sel = ", ".join(parts)
    q = f"SELECT {sel} FROM w"
    r = rng.random()
    if r < 0.1:
        q += " QUALIFY row_number() OVER (ORDER BY k) <= 3"
    elif r < 0.18:
        q = f"SELECT * FROM (SELECT {sel} FROM w) ORDER BY ALL"
    if nodiff:
        q += " /*POSITIONAL*/"          # bottomup's child skips the value comparison for this marker (crash/INTERNAL still checked)
    return q


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--chunk", type=int, default=40)
    ap.add_argument("--emit", default="", help="write self-contained scripts as JSONL (plain + enable_verification) and exit")
    a = ap.parse_args()

    rng = random.Random(a.seed)
    qs = list(dict.fromkeys(build(rng) for _ in range(a.n)))
    if a.emit:
        pre = ";\n".join(SETUP) + ";\n"
        with open(a.emit, "w") as fh:
            for i, q in enumerate(qs):
                for tag, v in (("plain", ""), ("verify", "PRAGMA enable_verification;\n")):
                    fh.write(json.dumps({"id": f"win-{a.seed}-{i}:{tag}", "sql": v + pre + q + ";\n"}) + "\n")
        print(f"wrote {2 * len(qs)} scripts to {a.emit}")
        return

    p = devcheck.DEV_PY
    pr = __import__("subprocess").run([str(p), "-c", "import duckdb;print(','.join(r[0] for r in duckdb.connect().execute('select name from duckdb_optimizers()').fetchall()))"],
                                      capture_output=True, text=True, timeout=60)
    noopt = "SET disabled_optimizers='" + pr.stdout.strip().splitlines()[-1] + "'"
    cases = [{"id": i, "q": q} for i, q in enumerate(qs)]
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"DuckDB {devcheck.version()}: {len(cases)} distinct window queries", flush=True)
    seen: Counter = Counter()
    t0 = time.time()
    with open(OUT / f"hits-seed{a.seed}.jsonl", "w") as fh:
        for mode, extra in (("plain", []), ("verify", ["PRAGMA enable_verification"])):
            bottomup.SETUP = extra + SETUP
            for i in range(0, len(cases), a.chunk):
                for f in bottomup.run_chunk(cases[i:i + a.chunk], noopt):
                    s = f"{mode}:" + bottomup.sig(f["kind"], f["detail"])
                    seen[s] += 1
                    if seen[s] == 1:
                        f["mode"] = mode
                        f["sql"] = next((c["q"] for c in cases if c["id"] == f["id"]), None)
                        fh.write(json.dumps(f) + "\n")
                        fh.flush()
                        print(f"[{mode} {f['id']}] {f['kind']:10} {f['detail'][:120]}", flush=True)
            print(f"... {mode} pass done {time.time() - t0:.0f}s", flush=True)
    print(f"done in {time.time() - t0:.0f}s: {len(seen)} unique signatures", flush=True)


if __name__ == "__main__":
    main()
