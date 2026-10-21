"""Function fuzzer: call every new (or changed) DuckDB function with edge-case arguments.

    python harness/fuzz_functions.py --old-python /path/to/older/venv/bin/python --workers 3

1. Reads every overload from duckdb_functions() on the target build (the python wheel).
2. With --old-python, keeps only overloads that did not exist in the older version
   (new functions and new overloads of old functions); without it, fuzzes everything.
3. For each scalar overload builds argument combinations from edge-value pools per type
   (NULLs, extremes, empty / 2049-long values, infinity, ...) and runs three variants:
     A constant call            SELECT f(CAST(v AS T), ...)             (constant folding on)
     B row call, optimizer off  SELECT f(c0, ...) FROM (VALUES (...))   (no folding)
     C 2049 identical rows      SELECT f(c0, ...) FROM (... range(2049)) (vector boundary)
   Oracles: INTERNAL error, crash / hang of the worker, A vs B result, all rows of C equal A.
   Aggregates / macros: crash and INTERNAL-error checks only.
4. Findings are de-duplicated and written to results/funcfuzz/ (hits.jsonl, FUNC_REPORT.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import devcheck  # noqa: E402
import oracle  # noqa: E402
import runner  # noqa: E402

WORK = runner.WORK
OUT = WORK / "results" / "funcfuzz"

INTS = ["0", "1", "-1", "127", "255", "32767", "65535", "2147483647", "-2147483648", "4294967295", "NULL"]
BIGS = INTS + ["9223372036854775807", "-9223372036854775808"]
HUGE = BIGS + ["170141183460469231731687303715884105727", "-170141183460469231731687303715884105728"]
FLOATS = ["0", "-0.0", "1", "-1", "1e308", "-1e308", "1e-320", "'nan'", "'inf'", "'-inf'", "NULL"]
DECS = ["0", "1.5", "-9999999.99", "NULL"]
STRS = ["''", "'a'", "'ä€\U0001F600'", "repeat('x',2049)", "'%'", "'NULL'", "'1'", "'\\\\'", "NULL"]
BOOLS = ["true", "false", "NULL"]
DATES = ["'1970-01-01'", "'0001-01-01'", "'9999-12-31'", "'infinity'", "'-infinity'", "NULL"]
TSS = ["'1970-01-01 00:00:00'", "'2000-02-29 23:59:59.999999'", "'infinity'", "'-infinity'", "NULL"]
TIMES = ["'00:00:00'", "'23:59:59.999999'", "NULL"]
INTERVALS = ["INTERVAL 0 DAY", "INTERVAL 1 MONTH", "INTERVAL (-1) YEAR", "INTERVAL 2147483647 DAY", "NULL"]
BLOBS = ["''::BLOB", "'\\x00'::BLOB", "NULL"]
UUIDS = ["'00000000-0000-0000-0000-000000000000'", "NULL"]
BITS = ["'0'", "'101'", "NULL"]
LISTS = ["[]", "[NULL]", "[1]", "[1,NULL,3]", "list(range(2049))", "NULL"]
GENERIC = ["1", "NULL", "'a'", "[1,2]", "{'a': 1}", "0.5", "true", "''"]

NONDET = re.compile(r"random|uuid|now|current_|today|nextval|currval|txid|getenv|version|sleep|setseed|"
                    r"pg_|duckdb_|sample|read_|glob|query|system|checkpoint|sniff|summary|describe|"
                    r"^(from|to)_(csv|json|parquet)|^(copy|attach|detach|install|load|set|reset|export|import)", re.I)
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

LISTER = r'''
import duckdb, json
c = duckdb.connect()
rows = c.execute("SELECT function_name, function_type, parameter_types, varargs, stability, has_side_effects, "
                 "return_type FROM duckdb_functions()").fetchall()
print(json.dumps([list(r) for r in rows]))
'''

CHILD = r'''
import duckdb, json, sys, threading
d = json.load(sys.stdin)
NOOPT, RESET = d["noopt"], "RESET disabled_optimizers"

def connect():
    c = duckdb.connect(":memory:")
    for s in d["pre"]:
        c.execute(s)
    c.execute("SET max_execution_time=20000")      # ms; DuckDB 2.0 setting, replaces a timer thread
    return c

try:
    c = connect()
except Exception as e:
    print(json.dumps({"fatal": str(e)[:200]}), flush=True); sys.exit(3)

def run(q, secs=15):
    try:
        return "ok", c.execute(q).fetchall()
    except Exception as e:
        m = str(e)
        name = type(e).__name__
        if name == "FatalException" or "FATAL Error" in m or "database has been invalidated" in m.lower():
            kind = "fatal"
        elif name == "InternalException" or "INTERNAL Error" in m:
            kind = "internal"
        else:
            kind = "err"
        return kind, name + ": " + " ".join(m.split())[:230]

def emit(o): print(json.dumps(o), flush=True)

for case in d["cases"]:
    emit({"at": case["id"]})
    try:
        c.execute(RESET)
    except Exception:
        c = connect()                       # a previous case invalidated the database: start clean
    if any(run("SELECT " + l)[0] != "ok" for l in case["lits"]):
        continue                            # an argument literal is not a valid value of its type: not comparable
    kind, a = run(case["qA"])
    res = {"A": (kind, a)}
    if case["kind"] == "scalar":
        try: c.execute(NOOPT)
        except Exception: c = connect(); c.execute(NOOPT)
        res["B"] = run(case["qB"])
        res["C"] = run(case["qC"], 40)
        try: c.execute(RESET)
        except Exception: c = connect()
    else:
        for k in ("qB", "qC"):
            if case.get(k):
                res[k[1]] = run(case[k])
    for k, (st, v) in res.items():
        if st in ("internal", "fatal"):
            emit({"id": case["id"], "kind": st, "variant": k, "detail": v})
    if any(st == "fatal" for st, _ in res.values()):
        c = connect()
        continue
    if case["kind"] == "scalar":
        (sa, va), (sb, vb), (sc, vc) = res["A"], res["B"], res["C"]
        if sa == "ok" and sb == "ok" and repr(va) != repr(vb):
            emit({"id": case["id"], "kind": "value_diff", "detail": "const=%s | values-no-opt=%s" % (repr(va)[:120], repr(vb)[:120])})
        elif (sa == "ok") != (sb == "ok") and "internal" not in (sa, sb) and not any(
                str(v).startswith(("BinderException", "CatalogException")) for v in (va, vb)):
            emit({"id": case["id"], "kind": "error_diff", "detail": "const=%s | values-no-opt=%s" % (str(va)[:110], str(vb)[:110])})
        if sa == "ok" and sc == "ok" and va:
            rows = [repr(r) for r in vc]
            if len(set(rows)) > 1 or (rows and rows[0] != repr(va[0])):
                emit({"id": case["id"], "kind": "vector_diff", "detail": "const=%s | 2049-rows distinct=%d first=%s" % (repr(va[0])[:100], len(set(rows)), (rows[0] if rows else "")[:100])})
emit({"done": True})
'''


def pool(t: str | None) -> tuple[list[str], str | None]:
    """(edge values, cast type or None). The cast target is the parameter type itself."""
    if t is None:
        return GENERIC, None
    u = t.upper().strip()
    if u.endswith("[]"):
        base = u[:-2]
        return LISTS, (None if base in ("ANY", "") else u)
    table = [(("TINYINT", "SMALLINT", "INTEGER", "UTINYINT", "USMALLINT", "UINTEGER"), INTS),
             (("BIGINT", "UBIGINT"), BIGS), (("HUGEINT", "UHUGEINT"), HUGE), (("FLOAT", "DOUBLE"), FLOATS),
             (("DECIMAL",), DECS), (("VARCHAR", "STRING", "TEXT"), STRS), (("BOOLEAN",), BOOLS), (("DATE",), DATES),
             (("TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS", "TIMESTAMPTZ"), TSS),
             (("TIME", "TIME WITH TIME ZONE"), TIMES), (("INTERVAL",), INTERVALS), (("BLOB",), BLOBS),
             (("UUID",), UUIDS), (("BIT",), BITS)]
    for names, vals in table:
        if u in names:
            return vals, u
    if re.fullmatch(r"[A-Z_][A-Z0-9_]*", u) and u not in ("ANY", "STRUCT", "MAP", "UNION", "ENUM", "LIST", "ARRAY",
                                                         "TABLE", "LAMBDA", "TEMPLATE", "TYPE", "ROW", "NULL"):
        return GENERIC, u                  # e.g. VARIANT, BIGNUM, JSON, GEOMETRY: cast the generic values to it
    return GENERIC, None


def cast(lit: str, t: str | None) -> str:
    return lit if t is None or lit == "NULL" and t is None else f"CAST({lit} AS {t})"


def make_cases(fn: str, ftype: str, ptypes: list, varargs: str | None, rng: random.Random, per: int,
               start_id: int) -> list[dict]:
    types = list(ptypes or [])
    if varargs is not None:
        types += [varargs] * rng.randint(1, 3)
    if len(types) > 6:
        return []
    pools = [pool(t) for t in types]
    combos: list[tuple[int, ...]] = []
    seen = set()

    def add(c):
        if c not in seen:
            seen.add(c)
            combos.append(c)
    n = len(types)
    if n == 0:
        add(())
    else:
        add(tuple(0 for _ in pools))
        add(tuple(len(p[0]) - 1 for p in pools))                       # last = usually NULL
        for _ in range(per * 3):
            if len(combos) >= per:
                break
            add(tuple(rng.randrange(len(p[0])) for p in pools))
    cases = []
    for k, combo in enumerate(combos):
        lits = [cast(pools[i][0][ix], pools[i][1]) for i, ix in enumerate(combo)]
        cols = [f"c{i}" for i in range(n)]
        args_lit = ", ".join(lits)
        args_col = ", ".join(cols)
        proj = ", ".join(f"{lits[i]} AS c{i}" for i in range(n)) or "1 AS c0"
        case = {"id": start_id + k, "fn": fn, "types": types, "lits": lits, "kind": "scalar" if ftype == "scalar" else "other",
                "args": args_lit, "qA": f"SELECT {fn}({args_lit})",
                "qB": f"SELECT {fn}({args_col}) FROM (SELECT {proj}) t",
                "qC": f"SELECT {fn}({args_col}) FROM (SELECT {proj} FROM range(2049)) t"}
        if ftype == "macro":              # may expand to an aggregate: only check for crashes / INTERNAL errors
            case["qC"] = None
        if ftype == "aggregate":
            case["qA"] = f"SELECT {fn}({args_col}) FROM (SELECT {proj} FROM range(2049)) t"
            case["qB"] = f"SELECT {fn}({args_col}) FROM (SELECT {proj} FROM range(2049)) t GROUP BY (range % 7)" \
                if False else f"SELECT {fn}({args_col}) OVER () FROM (SELECT {proj} FROM range(100)) t"
            case["qC"] = None
        cases.append(case)
    return cases


def sig_of(kind: str, fn: str, detail: str) -> str:
    return f"{kind}:{fn}:" + hashlib.sha1(re.sub(r"\d+", "N", detail[:140]).encode()).hexdigest()[:10]


def run_chunk(chunk: list[dict], pre: list[str], noopt: str, timeout: float = 240.0) -> list[dict]:
    """Run cases in one child; if it dies or hangs, blame the case after the last progress marker."""
    findings: list[dict] = []
    todo = list(chunk)
    by_id = {c["id"]: c for c in chunk}
    while todo:
        payload = json.dumps({"pre": pre, "noopt": noopt, "cases": todo})
        try:
            p = subprocess.run([str(devcheck.DEV_PY), "-c", CHILD], input=payload, capture_output=True, text=True,
                               timeout=timeout, cwd=WORK / "duckdb")
            out, rc, timed_out = p.stdout, p.returncode, False
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            rc, timed_out = -999, True
        last = None
        finished = False
        for ln in out.splitlines():
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if "at" in o:
                last = o["at"]
            elif "done" in o:
                finished = True
            elif "fatal" in o:
                findings.append({"kind": "setup_error", "detail": o["fatal"], "id": None})
                return findings
            elif "kind" in o:
                findings.append(o)
        if finished:
            return findings
        culprit = by_id.get(last)
        kind = "timeout" if timed_out else "crash"
        findings.append({"id": last, "kind": kind, "detail": f"worker {'hung' if timed_out else 'died'} rc={rc}"})
        if culprit is None:
            return findings
        todo = [c for c in todo if c["id"] > last]
    return findings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old-python", default="", help="python of an older DuckDB venv: fuzz only overloads that are new vs it")
    ap.add_argument("--per-overload", type=int, default=24)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--chunk", type=int, default=30)
    ap.add_argument("--modes", default="default,vec_vectors", help="default,vec_vectors,vec_functions,vec_serialization")
    ap.add_argument("--only", default="", help="regex: only functions whose name matches")
    ap.add_argument("--limit-functions", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    ok, detail = devcheck.setting_works("VERIFY_VECTORS")
    if not ok:
        sys.exit(f"guard failed: debug_verification_mode did not apply ({detail})")
    ver = devcheck.version()

    def list_overloads(py: str) -> list:
        p = subprocess.run([py, "-c", LISTER], capture_output=True, text=True, timeout=120)
        return json.loads(p.stdout.strip().splitlines()[-1])

    new = list_overloads(str(devcheck.DEV_PY))
    if a.old_python:
        old = {(r[0], tuple(r[2] or [])) for r in list_overloads(a.old_python)}
        new = [r for r in new if (r[0], tuple(r[2] or [])) not in old]
    rng = random.Random(a.seed)
    cases: list[dict] = []
    skipped = Counter()
    seen_ov = set()
    only = re.compile(a.only) if a.only else None
    fnames = sorted({r[0] for r in new if IDENT.match(r[0]) and not r[0].startswith(("__", "collate_"))})
    if a.limit_functions:
        rng.shuffle(fnames)
        fnames = fnames[:a.limit_functions]
    keep = set(fnames)
    for name, ftype, ptypes, varargs, stability, side, ret in new:
        key = (name, ftype, tuple(ptypes or []))
        if name not in keep or key in seen_ov:
            continue
        seen_ov.add(key)
        if only and not only.search(name):
            continue
        if not IDENT.match(name) or name.startswith("__") or name.startswith("collate_"):
            skipped["not callable / internal"] += 1
        elif side or stability == "VOLATILE" or NONDET.search(name):
            skipped["volatile / side effects / io"] += 1
        elif ftype not in ("scalar", "aggregate", "macro"):
            skipped[f"type {ftype}"] += 1
        else:
            cases += make_cases(name, ftype, ptypes or [], varargs, rng, a.per_overload, len(cases))
    for i, c in enumerate(cases):
        c["id"] = i
    print(f"DuckDB {ver}: {len(fnames)} functions selected, {len(seen_ov)} overloads, {len(cases)} cases "
          f"(x{len(a.modes.split(','))} modes) | skipped: {dict(skipped)}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    allopts = oracle.OPTIMIZERS
    noopt = "SET disabled_optimizers='" + ",".join(allopts) + "'"
    mode_pre = {"default": [], "vec_vectors": ["SET GLOBAL debug_verification_mode='VERIFY_VECTORS'"],
                "vec_functions": ["SET GLOBAL debug_verification_mode='VERIFY_FUNCTIONS'"],
                "vec_serialization": ["SET GLOBAL debug_verification_mode='VERIFY_SERIALIZATION'"]}
    jobs = []
    for m in a.modes.split(","):
        for i in range(0, len(cases), a.chunk):
            jobs.append((m, cases[i:i + a.chunk]))
    t0 = time.time()
    hits: dict[str, dict] = {}
    counts: Counter = Counter()
    by_id = {c["id"]: c for c in cases}
    with ThreadPoolExecutor(a.workers) as ex, open(OUT / "hits.jsonl", "w") as fh:
        for n, (m, res) in enumerate(ex.map(lambda j: (j[0], run_chunk(j[1], mode_pre[j[0]], noopt)), jobs), 1):
            for f in res:
                case = by_id.get(f.get("id"), {})
                fn = case.get("fn", "?")
                sig = sig_of(f["kind"], fn, f.get("detail", ""))
                counts[f["kind"]] += 1
                rec = {"sig": sig, "kind": f["kind"], "mode": m, "fn": fn, "types": case.get("types"),
                       "detail": f.get("detail"), "variant": f.get("variant"),
                       "qA": case.get("qA"), "qB": case.get("qB"), "qC": case.get("qC")}
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                if sig not in hits:
                    hits[sig] = rec
                    if f["kind"] in ("internal", "fatal", "crash", "timeout", "value_diff", "vector_diff"):
                        print(f"[{n}/{len(jobs)}] {f['kind']:11s} {m:11s} {fn}({','.join(map(str, case.get('types') or []))}) :: "
                              f"{str(f.get('detail'))[:110]}", flush=True)
            if n % 20 == 0:
                print(f"... {n}/{len(jobs)} chunks {dict(counts)} {time.time()-t0:.0f}s", flush=True)
    print(f"done in {time.time()-t0:.0f}s: findings {dict(counts)}; unique {len(hits)}", flush=True)

    order = {"crash": 0, "fatal": 0, "internal": 1, "value_diff": 2, "vector_diff": 3, "timeout": 4, "error_diff": 5, "setup_error": 6}
    L = [f"# Function fuzzer report - DuckDB {ver} - {time.strftime('%Y-%m-%d %H:%M')}", "",
         f"{len(cases)} cases per mode, modes {a.modes}; {len(hits)} unique findings: {dict(counts)}", "",
         "| kind | function | arg types | mode | detail |", "|---|---|---|---|---|"]
    for r in sorted(hits.values(), key=lambda r: order.get(r["kind"], 9)):
        L.append(f"| {r['kind']} | {r['fn']} | {', '.join(map(str, r['types'] or []))} | {r['mode']} | "
                 f"{str(r['detail'])[:90].replace('|', '/')} |")
    for r in sorted(hits.values(), key=lambda r: order.get(r["kind"], 9))[:25]:
        L += ["", f"## {r['kind']}: {r['fn']}", "```sql", str(r["qA"]), str(r["qB"]), str(r["qC"] or ""), "```", str(r["detail"])]
    (OUT / "FUNC_REPORT.md").write_text("\n".join(L) + "\n")
    print(f"report: {OUT / 'FUNC_REPORT.md'}", flush=True)


if __name__ == "__main__":
    main()
