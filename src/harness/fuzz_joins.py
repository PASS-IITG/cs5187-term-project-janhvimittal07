"""JoinEquiv-style oracle: INNER / NATURAL JOIN vs its set-theoretic equivalents (arXiv 2606.23294).

    python harness/fuzz_joins.py --n 20000 --seed 1

For two NOT NULL tables the inner join is the intersection of the LEFT and RIGHT outer joins:
  Q                                  SELECT cols FROM t0 [NATURAL] JOIN t1 WHERE p
  SJT  (symmetric join transform)    (... NATURAL LEFT JOIN ... WHERE p) INTERSECT ALL (... NATURAL RIGHT JOIN ... WHERE p)
  ADT  (asymmetric difference)       L EXCEPT ALL (L EXCEPT ALL R)       with L / R the LEFT / RIGHT join results
NOT NULL columns matter: with NULLs the padding of an outer join would create spurious differences.
The upstream bugs found this way (#20483, #20486, #20608) needed what our other generators lack: NATURAL JOIN on key
columns of *different* types (BOOL vs DOUBLE, INT vs VARCHAR), PRIMARY KEY / UNIQUE (ART index) tables, and
INTERSECT ALL / EXCEPT ALL. Here every case randomises exactly those. Findings: result mismatch between Q and an
equivalent, INTERNAL / FATAL error, worker crash.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import devcheck  # noqa: E402
import runner  # noqa: E402

WORK = runner.WORK
OUT = WORK / "results" / "joins"

TYPES = {                     # small, overlapping value pools so that joins on mixed-type keys really match rows
    "BOOL": ["true", "false"],
    "TINYINT": ["0", "1", "2"],
    "INT": ["0", "1", "2", "-1"],
    "BIGINT": ["0", "1", "2"],
    "DOUBLE": ["0", "1", "2", "0.5", "-0.0"],
    "DECIMAL(10,2)": ["0", "1", "2", "0.50"],
    "VARCHAR": ["'0'", "'1'", "'2'", "'true'", "'a'", "'A'", "'cOB4'", "'cob4'"],
    "DATE": ["DATE '2020-01-01'", "DATE '1999-12-31'"],
}
TNAMES = list(TYPES)


def build(rng: random.Random) -> dict:
    ncols = [rng.randint(1, 3), rng.randint(1, 3)]
    names = [["c0", "c1", "c2"][:n] for n in ncols]
    if not set(names[0]) & set(names[1]):
        names[1][0] = "c0"
    tabs = []
    setup = []
    for ti in range(2):
        cols = []
        for cn in names[ti]:
            t = rng.choice(TNAMES)
            cols.append((cn, t))
        pk = ""
        r = rng.random()
        if r < 0.45:
            pk = f", PRIMARY KEY({cols[0][0]})"
        elif r < 0.55 and len(cols) > 1:
            pk = f", PRIMARY KEY({cols[0][0]}, {cols[1][0]})"
        elif r < 0.65:
            pk = f", UNIQUE({cols[0][0]})"
        defs = ", ".join(f"{n} {t} NOT NULL" for n, t in cols)
        setup.append(f"CREATE TABLE t{ti}({defs}{pk})")
        ins = "INSERT OR IGNORE" if pk else "INSERT"          # OR IGNORE needs a key constraint
        for _ in range(rng.randint(2, 7)):
            vals = ", ".join(rng.choice(TYPES[t]) for _, t in cols)
            setup.append(f"{ins} INTO t{ti} VALUES ({vals})")
        tabs.append(cols)
    shared = [c for c, _ in tabs[0] if c in {x for x, _ in tabs[1]}]
    # explicit select list: the first table's columns plus the second table's own columns
    sel = ", ".join([f"t0.{c}" for c, _ in tabs[0]] + [f"t1.{c}" for c, _ in tabs[1] if c not in shared])
    allcols = [f"t0.{c}" for c, _ in tabs[0]] + [f"t1.{c}" for c, _ in tabs[1]]
    consts = ["true", "false", "0", "1", "2", "0.5", "'a'", "'1'", "NULL"]
    preds = []
    for _ in range(rng.randint(0, 2)):
        c = rng.choice(allcols)
        op = rng.choice(["= {k}", "<> {k}", "IN ({k})", "> {k}", "IS NOT NULL", "IS TRUE", "BETWEEN {k} AND {k2}"])
        preds.append(f"{c} " + op.format(k=rng.choice(consts), k2=rng.choice(consts)))
    p = " AND ".join(preds) if preds else "true"
    natural = rng.random() < 0.7
    j = "NATURAL " if natural else ""
    on = "" if natural else f" USING ({', '.join(shared)})" if rng.random() < 0.5 else " ON " + " AND ".join(
        f"t0.{c} = t1.{c}" for c in shared)

    def q(kind: str) -> str:
        return f"SELECT {sel} FROM t0 {j}{kind} JOIN t1{on} WHERE {p}"

    inner = f"SELECT {sel} FROM t0 {j}JOIN t1{on} WHERE {p}"
    left, right = q("LEFT"), q("RIGHT")
    sjt = f"({left}) INTERSECT ALL ({right})"
    adt = f"({left}) EXCEPT ALL (({left}) EXCEPT ALL ({right}))"
    return {"setup": setup, "q": inner, "sjt": sjt, "adt": adt}


CHILD = r'''
import duckdb, json, sys
d = json.load(sys.stdin)

def run(c, q):
    try:
        return "ok", sorted(repr(r) for r in c.execute(q).fetchall())
    except Exception as e:
        m = str(e); n = type(e).__name__
        k = "fatal" if n == "FatalException" or "FATAL Error" in m else "internal" if n == "InternalException" or "INTERNAL Error" in m else "err"
        return k, n + ": " + " ".join(m.split())[:200]

def emit(o): print(json.dumps(o), flush=True)

for case in d["cases"]:
    emit({"at": case["id"]})
    c = duckdb.connect(":memory:")
    c.execute("SET max_execution_time=20000"); c.execute("SET memory_limit='1GB'"); c.execute("SET threads=2")
    if d["verify"]:
        c.execute("PRAGMA enable_verification")
    try:
        for s in case["setup"]:
            c.execute(s)
    except Exception:
        continue
    base = run(c, case["q"])
    for name in ("sjt", "adt"):
        eq = run(c, case[name])
        for k, v in ((base[0], base[1]), (eq[0], eq[1])):
            if k in ("internal", "fatal"):
                emit({"id": case["id"], "kind": k, "variant": name, "detail": v if isinstance(v, str) else str(v)[:200]})
        if base[0] == "ok" and eq[0] == "ok" and base[1] != eq[1]:
            emit({"id": case["id"], "kind": "join_mismatch", "variant": name,
                  "detail": "%s: join %d rows %s | equivalent %d rows %s" % (name, len(base[1]), base[1][:3], len(eq[1]), eq[1][:3])})
    c.close()
emit({"done": True})
'''


def run_chunk(chunk: list[dict], verify: bool, timeout: float = 180.0) -> list[dict]:
    findings: list[dict] = []
    todo = list(chunk)
    while todo:
        err = ""
        try:
            p = subprocess.run([str(devcheck.DEV_PY), "-c", CHILD], input=json.dumps({"cases": todo, "verify": verify}),
                               capture_output=True, text=True, timeout=timeout)
            out, rc, hung = p.stdout, p.returncode, False
            err = p.stderr.strip().splitlines()[-1][:150] if p.stderr.strip() else ""
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            rc, hung = -999, True
        last, done = None, False
        for ln in out.splitlines():
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if "at" in o:
                last = o["at"]
            elif "done" in o:
                done = True
            elif "kind" in o:
                findings.append(o)
        if done:
            return findings
        findings.append({"id": last, "kind": "timeout" if hung else "crash", "detail": f"worker rc={rc} {err}"})
        if last is None:
            return findings
        todo = [c for c in todo if c["id"] > last]
    return findings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--chunk", type=int, default=100)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    cases = [dict(build(rng), id=i) for i in range(a.n)]
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"DuckDB {devcheck.version()}: {len(cases)} join cases", flush=True)
    seen: Counter = Counter()
    t0 = time.time()
    by_id = {c["id"]: c for c in cases}
    with open(OUT / f"hits-seed{a.seed}.jsonl", "w") as fh:
        for mode in (False, True):
            for i in range(0, len(cases), a.chunk):
                for f in run_chunk(cases[i:i + a.chunk], mode):
                    sig = f"{'verify' if mode else 'plain'}:{f['kind']}:{re.sub(r'[0-9]+', 'N', f['detail'])[:80]}"
                    seen[sig] += 1
                    if seen[sig] == 1:
                        c = by_id.get(f["id"], {})
                        f.update(verify=mode, setup=c.get("setup"), q=c.get("q"), sjt=c.get("sjt"), adt=c.get("adt"))
                        fh.write(json.dumps(f) + "\n")
                        fh.flush()
                        print(f"[{'verify' if mode else 'plain'} {f['id']}] {f['kind']:13} {f['detail'][:130]}", flush=True)
            print(f"... {'verify' if mode else 'plain'} pass done {time.time() - t0:.0f}s", flush=True)
    print(f"done in {time.time() - t0:.0f}s: {len(seen)} unique signatures", flush=True)


if __name__ == "__main__":
    main()
