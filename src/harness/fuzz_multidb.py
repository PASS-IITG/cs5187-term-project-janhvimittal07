"""Database-independent logic-bug / crash fuzzer: the same generator and oracles run against several engines.

    python harness/fuzz_multidb.py --db sqlite --seeds 1-20 --cases 200 --out results/multidb
    python harness/fuzz_multidb.py --db duckdb --seeds 1-5            # control: must reproduce nothing noisy

Oracles (each compares an engine with ITSELF, so dialect differences between engines do not matter):
  norec    count(*) WHERE p            ==  sum(CASE WHEN p THEN 1 ELSE 0 END)            (Rigger & Su, NoREC)
  tlp      count(*)                    ==  count(p) + count(NOT p) + count(p IS NULL)    (Ternary Logic Partitioning)
  tlp_agg  min/max/sum/count over t    ==  the same aggregate combined over the 3 partitions
  index    (SQLite only) the same WHERE with NOT INDEXED / with the indexes  must return the same rows
  crash    the worker process dies (signal / abnormal exit) or the engine reports an internal / corruption error
Edge families aim at overflow and memory handling: INT64 extremes in arithmetic, huge/negative substr/printf/zeroblob
arguments, long strings, NULL-heavy rows.

Each seed is executed by a child process so a segfault is attributed to the exact case (the case script is written to
disk before it runs). A hit is only reported after it reproduces on a fresh connection. Nothing here is a bug report:
check the engine version and upstream before believing a hit (the system SQLite may be old).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

# ----------------------------------------------------------------------------- dialects


class Dialect:
    name = "?"
    int_t, text_t, real_t = "INTEGER", "TEXT", "REAL"
    cast_int, cast_text, cast_real = "INTEGER", "TEXT", "REAL"
    greatest = "max"
    least = "min"
    internal_markers = ("internal", "malformed", "corrupt", "unknown error", "assertion", "database disk image")

    def connect(self):
        raise NotImplementedError

    def version(self):
        raise NotImplementedError

    def run(self, con, sql):
        """-> ('ok', rows) or ('err', message)."""
        raise NotImplementedError

    def is_internal(self, msg):
        m = msg.lower()
        return any(x in m for x in self.internal_markers)


class SQLite(Dialect):
    name = "sqlite"
    internal_markers = ("malformed", "corrupt", "unknown error", "internal", "disk i/o", "not an error", "bad parameter")

    def connect(self):
        import sqlite3
        con = sqlite3.connect(":memory:", isolation_level=None)
        con.execute("PRAGMA foreign_keys=OFF")
        return con

    def version(self):
        import sqlite3
        return "sqlite " + sqlite3.sqlite_version

    def run(self, con, sql):
        import sqlite3
        try:
            cur = con.execute(sql)
            rows = cur.fetchall()
            if os.environ.get("FUZZ_MULTIDB_FAULT") and "WHERE NOT (" in sql and rows and isinstance(rows[0][0], int) and rows[0][0] > 0:
                rows = [(rows[0][0] - 1,)]  # self-test only: proves the oracles can see a wrong result
            return "ok", rows
        except sqlite3.Error as e:
            return "err", f"{type(e).__name__}: {e}"
        except OverflowError as e:
            return "err", f"OverflowError: {e}"


class DuckDB(Dialect):
    name = "duckdb"
    int_t, text_t, real_t = "BIGINT", "VARCHAR", "DOUBLE"
    cast_int, cast_text, cast_real = "BIGINT", "VARCHAR", "DOUBLE"
    greatest, least = "greatest", "least"
    internal_markers = ("internal error", "fatal error", "assertion")

    def connect(self):
        import duckdb
        con = duckdb.connect()
        con.execute("SET threads=1")
        con.execute("SET memory_limit='1GB'")
        return con

    def version(self):
        import duckdb
        return "duckdb " + duckdb.__version__

    def run(self, con, sql):
        import duckdb
        try:
            return "ok", con.execute(sql).fetchall()
        except duckdb.Error as e:
            return "err", f"{type(e).__name__}: {e}"


DIALECTS = {"sqlite": SQLite, "duckdb": DuckDB}

# ----------------------------------------------------------------------------- generator

INT_EDGE = [0, 1, -1, 2, 255, 256, 65535, 2**31 - 1, -2**31, 2**32, 2**53, 2**63 - 1, -2**63, -2**63 + 1, 2**63 - 2]
STR_EDGE = ["", "a", "A", "ab", " ", "%", "_", "0", "-1", "1e2", "é", "x" * 100, "abc" * 300]
REAL_EDGE = [0.0, -0.0, 0.5, -1.5, 1e308, -1e308, 1e-300, 3.14159, 1e15, 9.007199254740993e15]


def lit(rng, d, kind):
    if rng.random() < 0.12:
        return "NULL"
    if kind == "i":
        return str(rng.choice(INT_EDGE + [rng.randint(-50, 50)] * 6))
    if kind == "t":
        return "'" + rng.choice(STR_EDGE).replace("'", "''") + "'"
    return repr(rng.choice(REAL_EDGE + [round(rng.uniform(-10, 10), 2)] * 5))


class Schema:
    def __init__(self, rng, d):
        self.d = d
        self.tables = {}
        self.stmts = []
        for ti in range(rng.randint(1, 3)):
            cols = []
            for ci in range(rng.randint(2, 4)):
                kind = rng.choice("iitr")
                cols.append((f"c{ci}", kind))
            tn = f"t{ti}"
            self.tables[tn] = cols
            tmap = {"i": d.int_t, "t": d.text_t, "r": d.real_t}
            defs = []
            for ci, (cn, k) in enumerate(cols):
                extra = ""
                if ci == 0 and rng.random() < 0.25:
                    extra = rng.choice([" PRIMARY KEY", " UNIQUE"])
                defs.append(f"{cn} {tmap[k]}{extra}")
            self.stmts.append(f"CREATE TABLE {tn}({', '.join(defs)})")
            if rng.random() < 0.6:
                c = rng.choice(cols)[0]
                self.stmts.append(f"CREATE INDEX i_{tn}_{c} ON {tn}({c})")
            for _ in range(rng.randint(0, 25)):
                vals = ", ".join(lit(rng, d, k) for _, k in cols)
                self.stmts.append(f"INSERT INTO {tn} VALUES ({vals})")
            if rng.random() < 0.3:
                self.stmts.append(f"DELETE FROM {tn} WHERE {rng.choice(cols)[0]} IS NULL")


def expr(rng, d, cols, depth=0):
    """cols: list of (qualified name, kind). Mixed-type expressions are allowed on purpose."""
    if depth > 2 or rng.random() < 0.3:
        if rng.random() < 0.65 and cols:
            return rng.choice(cols)[0]
        return lit(rng, d, rng.choice("iitr"))
    e = lambda: expr(rng, d, cols, depth + 1)
    r = rng.random()
    if r < 0.18:
        return f"({e()} {rng.choice(['+', '-', '*', '/', '%'])} {e()})"
    if r < 0.38:
        return f"({e()} {rng.choice(['=', '<>', '<', '<=', '>', '>='])} {e()})"
    if r < 0.46:
        return f"({e()} {rng.choice(['AND', 'OR'])} {e()})"
    if r < 0.50:
        return f"(NOT {e()})"
    if r < 0.56:
        return f"({e()} IS {rng.choice(['', 'NOT '])}NULL)"
    if r < 0.60:
        return f"({e()} BETWEEN {e()} AND {e()})"
    if r < 0.65:
        return f"({e()} {rng.choice(['', 'NOT '])}IN ({e()}, {e()}, {e()}))"
    if r < 0.69:
        return f"({e()} LIKE {lit(rng, d, 't')})"
    if r < 0.75:
        return f"(CASE WHEN {e()} THEN {e()} ELSE {e()} END)"
    if r < 0.80:
        return f"COALESCE({e()}, {e()})"
    if r < 0.83:
        return f"NULLIF({e()}, {e()})"
    if r < 0.87:
        return f"ABS({e()})"
    if r < 0.90:
        return f"CAST({e()} AS {rng.choice([d.cast_int, d.cast_text, d.cast_real])})"
    if r < 0.93:
        return f"{rng.choice(['LENGTH', 'LOWER', 'UPPER'])}({e()})"
    if r < 0.96:
        return f"SUBSTR({e()}, {e()}, {e()})"
    if r < 0.98:
        return f"({e()} || {e()})"
    return f"{rng.choice([d.greatest, d.least])}({e()}, {e()})"


def edge_funcs(rng, d):
    """Overflow / memory probes (one engine call, extreme arguments)."""
    a, b = rng.choice(INT_EDGE), rng.choice(INT_EDGE)
    s = "'" + rng.choice(STR_EDGE) + "'"
    if d.name == "sqlite":
        pool = [f"SELECT substr({s}, {a}, {b})", f"SELECT substr(x'00ff', {a}, {b})", f"SELECT printf('%*d', {a % 4096}, {b})",
                f"SELECT printf('%.*f', {abs(a) % 400}, {rng.choice(REAL_EDGE)})", f"SELECT hex(zeroblob({abs(a) % 100000}))",
                f"SELECT length(zeroblob({min(abs(a), 2**31)}))", f"SELECT round({rng.choice(REAL_EDGE)}, {a})", f"SELECT abs({a})",
                f"SELECT {a} + {b}", f"SELECT {a} * {b}", f"SELECT {a} - {b}", f"SELECT -({a})", f"SELECT {a} % {b}", f"SELECT {a} / {b}",
                f"SELECT {a} << {b}", f"SELECT {a} >> {b}", f"SELECT {a} & {b} | ~{a}", f"SELECT sum(c) FROM (SELECT {a} AS c UNION ALL SELECT {b} UNION ALL SELECT {a})",
                f"SELECT total(c), avg(c) FROM (SELECT {a} AS c UNION ALL SELECT {b})", f"SELECT instr({s}, substr({s}, {a}, {b}))",
                f"SELECT replace({s}, substr({s}, 1, 1), {s} || {s})", f"SELECT char({abs(a) % 1114112})", f"SELECT unicode(char({abs(a) % 1114112}))",
                f"SELECT trim({s}, {s})", f"SELECT quote({s}), quote(zeroblob({abs(a) % 3000}))", f"SELECT CAST({s} AS INTEGER), CAST({a} AS TEXT)",
                f"SELECT {s} LIKE {s} ESCAPE '%'", f"SELECT glob({s}, {s})", f"SELECT randomblob({abs(a) % 100000}) IS NOT NULL",
                f"SELECT json_extract('{{\"a\":[1,2,3]}}', '$.a[{a % 10}]')", f"SELECT strftime('%Y-%m-%d', {a})", f"SELECT date({a}), datetime({b}, 'unixepoch')",
                f"SELECT julianday({rng.choice(REAL_EDGE)})", f"SELECT likely({a}), unlikely({b}), iif({a}, {b}, {a})"]
    else:
        pool = [f"SELECT substr({s}, {a}, {b})", f"SELECT ({a})::BIGINT + ({b})::BIGINT", f"SELECT ({a})::BIGINT * ({b})::BIGINT",
                f"SELECT -(({a})::BIGINT)", f"SELECT abs(({a})::BIGINT)", f"SELECT ({a})::BIGINT // ({b})::BIGINT",
                f"SELECT repeat({s}, {abs(a) % 5000})", f"SELECT lpad({s}, {abs(a) % 100000}, 'x')"]
    return rng.choice(pool)


def gen_case(seed, idx, d):
    rng = random.Random(f"{seed}:{idx}")
    sch = Schema(rng, d)
    tn = rng.choice(list(sch.tables))
    cols = [(f"{tn}.{c}", k) for c, k in sch.tables[tn]]
    use_join = len(sch.tables) > 1 and rng.random() < 0.4
    frm = tn
    if use_join:
        tn2 = rng.choice([t for t in sch.tables if t != tn])
        cols2 = [(f"{tn2}.{c}", k) for c, k in sch.tables[tn2]]
        cols = cols + cols2
        jp = expr(rng, d, cols, 1)
        frm = f"{tn} {rng.choice(['JOIN', 'LEFT JOIN'])} {tn2} ON {jp}"
    p = expr(rng, d, cols)
    checks = []
    checks.append(("norec", f"SELECT count(*) FROM {frm} WHERE {p}",
                   f"SELECT COALESCE(SUM(CASE WHEN {p} THEN 1 ELSE 0 END), 0) FROM {frm}"))
    checks.append(("tlp", f"SELECT count(*) FROM {frm}",
                   f"SELECT (SELECT count(*) FROM {frm} WHERE {p}) + (SELECT count(*) FROM {frm} WHERE NOT ({p})) + "
                   f"(SELECT count(*) FROM {frm} WHERE ({p}) IS NULL)"))
    agg = rng.choice(["min", "max", "count"])
    target = rng.choice(cols)[0]
    # combine partitions with the aggregate itself over a UNION ALL: unlike scalar min()/max() it ignores NULL partitions
    comb = {"min": f"SELECT min(v) FROM (SELECT min({target}) AS v FROM {frm} WHERE {p} UNION ALL SELECT min({target}) FROM {frm} WHERE NOT ({p}) UNION ALL SELECT min({target}) FROM {frm} WHERE ({p}) IS NULL)",
            "max": f"SELECT max(v) FROM (SELECT max({target}) AS v FROM {frm} WHERE {p} UNION ALL SELECT max({target}) FROM {frm} WHERE NOT ({p}) UNION ALL SELECT max({target}) FROM {frm} WHERE ({p}) IS NULL)",
            "count": f"SELECT (SELECT count({target}) FROM {frm} WHERE {p}) + (SELECT count({target}) FROM {frm} WHERE NOT ({p})) + (SELECT count({target}) FROM {frm} WHERE ({p}) IS NULL)"}[agg]
    checks.append(("tlp_agg", f"SELECT {agg}({target}) FROM {frm}", comb))
    if d.name == "sqlite" and not use_join:
        checks.append(("index", f"SELECT * FROM {tn} NOT INDEXED WHERE {p}", f"SELECT * FROM {tn} WHERE {p}"))
    edge = [edge_funcs(rng, d) for _ in range(3)]
    return sch.stmts, checks, edge


def norm(rows):
    def one(v):
        if isinstance(v, float):
            if v != v:
                return "nan"
            return round(v, 6) if abs(v) < 1e15 else float(f"{v:.6e}")
        if isinstance(v, bytes):
            return v.hex()
        if isinstance(v, int) and not isinstance(v, bool):
            return float(v) if abs(v) > 2**53 else v  # engines may switch to float on overflow
        return v
    return sorted((tuple(one(x) for x in r) for r in rows), key=lambda r: [str(x) for x in r])


# ----------------------------------------------------------------------------- worker / driver


def worker(dname, seed, cases, outdir):
    d = DIALECTS[dname]()
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    cur = outdir / f"current-{dname}-{seed}.sql"
    hits = []
    for i in range(cases):
        stmts, checks, edge = gen_case(seed, i, d)
        script = ";\n".join(stmts) + ";\n-- checks\n" + "\n".join(f"-- [{n}] A: {a}\n-- [{n}] B: {b}" for n, a, b in checks) + "\n-- edge\n" + ";\n".join(edge) + ";\n"
        cur.write_text(script)  # written BEFORE running: if the process dies, this is the culprit
        con = d.connect()
        ok = True
        for s in stmts:
            st, r = d.run(con, s)
            if st == "err" and d.is_internal(r):
                hits.append({"kind": "internal", "case": i, "sql": s, "msg": r, "script": script})
        for name, a, b in checks:
            sa, ra = d.run(con, a)
            sb, rb = d.run(con, b)
            for s_, r_ in ((sa, ra), (sb, rb)):
                if s_ == "err" and d.is_internal(r_):
                    hits.append({"kind": "internal", "case": i, "oracle": name, "msg": r_, "a": a, "b": b, "script": script})
            if sa == "ok" and sb == "ok":
                if norm(ra) != norm(rb):
                    hits.append({"kind": "mismatch", "oracle": name, "case": i, "a": a, "b": b, "ra": repr(ra)[:300], "rb": repr(rb)[:300], "script": script})
        for q in edge:
            st, r = d.run(con, q)
            if st == "err" and d.is_internal(r):
                hits.append({"kind": "internal", "case": i, "oracle": "edge", "sql": q, "msg": r, "script": script})
        con.close()
    cur.unlink(missing_ok=True)
    (outdir / f"hits-{dname}-{seed}.jsonl").write_text("".join(json.dumps(h) + "\n" for h in hits))
    print(json.dumps({"seed": seed, "cases": cases, "hits": len(hits)}))


def verify(dname, hit):
    """Replay the case script on a fresh connection and re-run the failing check; True if it still misbehaves."""
    d = DIALECTS[dname]()
    con = d.connect()
    lines = hit["script"].split("-- checks")[0]
    for s in [x for x in lines.split(";\n") if x.strip()]:
        d.run(con, s)
    if hit["kind"] == "mismatch":
        sa, ra = d.run(con, hit["a"])
        sb, rb = d.run(con, hit["b"])
        return sa == "ok" and sb == "ok" and norm(ra) != norm(rb)
    q = hit.get("sql") or hit.get("a")
    st, r = d.run(con, q)
    return st == "err" and d.is_internal(r)


def parse_seeds(s):
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in s.split(",")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", choices=list(DIALECTS), required=True)
    ap.add_argument("--seeds", default="1-5")
    ap.add_argument("--cases", type=int, default=200)
    ap.add_argument("--out", default="results/multidb")
    ap.add_argument("--worker", action="store_true")
    a = ap.parse_args()
    if a.worker:
        worker(a.db, int(a.seeds), a.cases, a.out)
        return
    d = DIALECTS[a.db]()
    print("engine:", d.version())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = {"engine": d.version(), "seeds": 0, "cases": 0, "crashes": [], "hits": []}
    t0 = time.time()
    for seed in parse_seeds(a.seeds):
        r = subprocess.run([sys.executable, "-I", __file__, "--db", a.db, "--seeds", str(seed), "--cases", str(a.cases), "--out", a.out, "--worker"],
                           capture_output=True, text=True, timeout=1800)
        summary["seeds"] += 1
        summary["cases"] += a.cases
        if r.returncode > 0:  # Python exception in the harness itself: NOT an engine crash
            summary.setdefault("harness_errors", []).append({"seed": seed, "stderr": r.stderr[-400:]})
            print(f"[HARNESS ERROR] seed {seed}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else '?'}")
            continue
        if r.returncode < 0:  # killed by a signal (SIGSEGV, SIGABRT, ...) => engine crash, attributed to the case on disk
            cur = out / f"current-{a.db}-{seed}.sql"
            keep = out / f"CRASH-{a.db}-{seed}.sql"
            if cur.exists():
                cur.rename(keep)
            summary["crashes"].append({"seed": seed, "rc": r.returncode, "stderr": r.stderr[-300:], "script": str(keep)})
            print(f"[CRASH] seed {seed} rc={r.returncode} -> {keep}")
            continue
        hf = out / f"hits-{a.db}-{seed}.jsonl"
        for line in hf.read_text().splitlines() if hf.exists() else []:
            h = json.loads(line)
            if verify(a.db, h):
                h["seed"] = seed
                h["sig"] = hashlib.sha1((h["kind"] + h.get("oracle", "") + h.get("msg", "")[:60]).encode()).hexdigest()[:10]
                summary["hits"].append(h)
    sigs = {}
    for h in summary["hits"]:
        sigs.setdefault(h["sig"], h)
    summary["distinct_signatures"] = len(sigs)
    summary["seconds"] = round(time.time() - t0)
    (out / f"SUMMARY-{a.db}.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in summary.items()}))
    for h in sigs.values():
        print(f"  [{h['kind']}/{h.get('oracle', '-')}] seed {h['seed']} case {h['case']}: {(h.get('msg') or h.get('a'))[:150]}")


if __name__ == "__main__":
    main()
