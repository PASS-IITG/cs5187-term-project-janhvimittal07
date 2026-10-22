"""Bottom-up query generator (inspired by SQLBull, Liang & Liu, NDSS 2026).

    python harness/bottomup.py --n 3000 --seed 1

A top-down generator starts at the grammar root and rarely reaches feature-rich constructs (POSITIONAL JOIN,
LATERAL, ASOF, correlated subqueries, recursive CTEs, ...). Here every query starts from one or two such
constructs and is built *upward*: each template takes an inner query Q (columns a INT, b VARCHAR) and returns
a bigger query with the same columns, so any template can wrap any other and the result stays valid.
Binary templates merge two such paths, like SQLBull's merged syntax paths.

Oracles (dev build): INTERNAL / FATAL error, worker crash, and result difference between the default plan
and a plan with every optimizer disabled. Findings go to results/bottomup/ (hits.jsonl).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import resource
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
OUT = WORK / "results" / "bottomup"

SETUP = [
    "CREATE TABLE t(a INT, b VARCHAR, c INT[])",
    "INSERT INTO t VALUES (1,'x',[1,2]), (2,'x',[]), (NULL,'y',NULL), (3,'z',[3]), (3,'z',[3,NULL]), (0,NULL,[0])",
    "CREATE TABLE t2(a INT, b VARCHAR, s STRUCT(x INT, y VARCHAR))",
    "INSERT INTO t2 VALUES (1,'x',{'x':1,'y':'p'}), (2,'y',{'x':2,'y':NULL}), (NULL,NULL,NULL)",
]

BASES = [
    "SELECT a, b FROM t",
    "SELECT a, b FROM t2",
    "SELECT a, b FROM t WHERE a IS NULL OR a > 1",
    "SELECT * FROM (VALUES (1,'x'),(NULL,'y'),(2,NULL)) v(a,b)",
    "SELECT range::INT AS a, 'k' || range AS b FROM range(5)",
    "SELECT a, b FROM t WHERE false",
]

# interesting (feature-rich) unary templates: inner query -> same two columns
UNARY = [
    "SELECT a, b FROM ({Q}) q QUALIFY row_number() OVER (PARTITION BY b ORDER BY a) = 1",
    "SELECT a, b FROM ({Q}) q WHERE a IN (SELECT t.a FROM t WHERE t.b = q.b)",
    "SELECT a, b FROM ({Q}) q WHERE NOT EXISTS (SELECT 1 FROM t WHERE t.a > q.a)",
    "SELECT (SELECT max(t.a) FROM t WHERE t.b = q.b) AS a, b FROM ({Q}) q",
    "WITH c AS MATERIALIZED ({Q}) SELECT a, b FROM c",
    "WITH RECURSIVE r(a,b) AS ({Q} UNION ALL SELECT a+1, b FROM r WHERE a < 3) SELECT a, b FROM r",
    "SELECT DISTINCT ON (b) a, b FROM ({Q}) q ORDER BY b, a",
    "SELECT sum(a) FILTER (WHERE a > 0) OVER (PARTITION BY b ORDER BY a ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS a, b FROM ({Q}) q",
    "SELECT a, b FROM ({Q}) q ORDER BY a DESC NULLS LAST, b LIMIT 4 OFFSET 1",
    "SELECT u.a, q.b FROM ({Q}) q, LATERAL (SELECT q.a + 1 AS a) u",
    "SELECT a, b FROM ({Q}) q GROUP BY GROUPING SETS ((a, b), (b), ())",
    "SELECT unnest([a, a + 1]) AS a, b FROM ({Q}) q",
    "SELECT list_transform([a, 1], lambda x: x + q.a)[1] AS a, b FROM ({Q}) q",
    "SELECT a, b FROM ({Q}) q WHERE a = ANY (SELECT unnest([1, 2, a]))",
    "SELECT min(a) AS a, b FROM ({Q}) q GROUP BY b HAVING count(*) >= 1",
    "SELECT a, b FROM ({Q}) q ORDER BY ALL",
    "SELECT a, min(b) OVER (ORDER BY a RANGE BETWEEN 1 PRECEDING AND 1 FOLLOWING) AS b FROM ({Q}) q",
    "SELECT a, b FROM ({Q}) q WHERE a BETWEEN (SELECT min(a) FROM t) AND (SELECT max(a) FROM t)",
]

# binary templates: merge two paths
BINARY = [
    "SELECT l.a, r.b FROM ({Q}) l POSITIONAL JOIN ({R}) r",
    "SELECT l.a, l.b FROM ({Q}) l ASOF JOIN ({R}) r ON l.b = r.b AND l.a >= r.a",
    "SELECT l.a, l.b FROM ({Q}) l SEMI JOIN ({R}) r ON l.a = r.a",
    "SELECT l.a, l.b FROM ({Q}) l ANTI JOIN ({R}) r ON l.a = r.a",
    "SELECT l.a, r.b FROM ({Q}) l, LATERAL (SELECT z.a, z.b FROM ({R}) z WHERE z.a >= l.a) r",
    "SELECT l.a, r.b FROM ({Q}) l FULL JOIN ({R}) r ON l.a = r.a",
    "SELECT l.a, l.b FROM ({Q}) l LEFT JOIN ({R}) r ON l.a = r.a AND l.b < r.b",
    "SELECT a, b FROM ({Q}) l UNION ALL BY NAME SELECT a, b FROM ({R}) r",
    "SELECT a, b FROM ({Q}) l INTERSECT SELECT a, b FROM ({R}) r",
    "SELECT a, b FROM ({Q}) l EXCEPT ALL SELECT a, b FROM ({R}) r",
    # the shape of the DuckDB PoC in the paper: POSITIONAL JOIN applied to the result of a LATERAL subquery
    "SELECT x.a, y.b FROM ({Q}) l, LATERAL (SELECT l.a AS a, l.b AS b) x POSITIONAL JOIN ({R}) y",
    "SELECT l.a, r.b FROM ({Q}) l JOIN ({R}) r ON l.a = r.a AND l.b IS NOT DISTINCT FROM r.b",
    "SELECT l.a, r.b FROM ({Q}) l JOIN LATERAL (SELECT z.b FROM ({R}) z WHERE z.a IS NOT DISTINCT FROM l.a) r ON true",
]


def build(rng: random.Random) -> str:
    """Pick 1-2 interesting seeds, then wrap them upward with random contexts and merge."""
    def path(depth: int) -> str:
        q = rng.choice(BASES)
        for _ in range(depth):
            q = rng.choice(UNARY).replace("{Q}", q)
        return q

    q = path(rng.randint(0, 2))
    if rng.random() < 0.7:                           # at least one binary construct most of the time
        q = rng.choice(BINARY).replace("{Q}", q).replace("{R}", path(rng.randint(0, 2)))
        for _ in range(rng.randint(0, 2)):
            q = rng.choice(UNARY).replace("{Q}", q)
    else:
        q = rng.choice(UNARY).replace("{Q}", q)
    return q


CHILD = r'''
import duckdb, json, sys
d = json.load(sys.stdin)
c = duckdb.connect(":memory:")
for s in d["setup"]:
    c.execute(s)
c.execute("SET max_execution_time=20000")
c.execute("SET memory_limit='1GB'")
c.execute("SET threads=2")

def run(q):
    try:
        return "ok", sorted(repr(r) for r in c.execute(q).fetchall())
    except Exception as e:
        m = str(e); n = type(e).__name__
        k = "fatal" if n == "FatalException" or "FATAL Error" in m else "internal" if n == "InternalException" or "INTERNAL Error" in m else "err"
        return k, n + ": " + " ".join(m.split())[:230]

def emit(o): print(json.dumps(o), flush=True)

for case in d["cases"]:
    emit({"at": case["id"]})
    c.execute("RESET disabled_optimizers")
    ka, a = run(case["q"])
    c.execute(d["noopt"])
    kb, b = run(case["q"])
    c.execute("RESET disabled_optimizers")
    for k, v, var in ((ka, a, "A"), (kb, b, "B")):
        if k in ("internal", "fatal"):
            emit({"id": case["id"], "kind": k, "variant": var, "detail": v})
    if "fatal" in (ka, kb):
        c = duckdb.connect(":memory:")
        for s in d["setup"]:
            c.execute(s)
        continue
    if ka == "ok" and kb == "ok" and a != b and "POSITIONAL" not in case["q"]:   # positional pairing depends on unordered input
        emit({"id": case["id"], "kind": "value_diff", "detail": "opt-on %d rows %s | opt-off %d rows %s" % (len(a), a[:3], len(b), b[:3])})
    elif (ka == "ok") != (kb == "ok") and "internal" not in (ka, kb) and "Out of Memory" not in str(a) + str(b):
        emit({"id": case["id"], "kind": "error_diff", "detail": "opt-on=%s | opt-off=%s" % (str(a)[:110], str(b)[:110])})
emit({"done": True})
'''


def _cap_memory() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (3 << 30, 3 << 30))


def confirm(case: dict | None, noopt: str) -> str:
    """Replay the blamed case alone in a fresh worker without the memory cap: does it still die?"""
    if case is None:
        return "no case to replay"
    try:
        p = subprocess.run([str(devcheck.DEV_PY), "-c", CHILD], input=json.dumps({"setup": SETUP, "noopt": noopt, "cases": [case]}),
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "hangs alone"
    return "reproduces alone" if p.returncode != 0 or '"done"' not in p.stdout else "NOT reproducible alone"


def run_chunk(chunk: list[dict], noopt: str, timeout: float = 180.0) -> list[dict]:
    findings: list[dict] = []
    todo = list(chunk)
    while todo:
        payload = json.dumps({"setup": SETUP, "noopt": noopt, "cases": todo})
        err = ""
        try:
            p = subprocess.run([str(devcheck.DEV_PY), "-c", CHILD], input=payload, capture_output=True, text=True,
                               timeout=timeout, preexec_fn=_cap_memory)
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
        culprit = next((c for c in chunk if c["id"] == last), None)
        findings.append({"id": last, "kind": "timeout" if hung else "crash",
                         "detail": f"worker {'hung' if hung else 'died'} rc={rc} {err} [{confirm(culprit, noopt)}]"})
        if last is None:
            return findings
        todo = [c for c in todo if c["id"] > last]
    return findings


def sig(kind: str, detail: str) -> str:
    d = re.sub(r"\d+", "N", detail)
    d = re.sub(r"\b(opt-on|opt-off)\b.*", "", d)[:90]
    return f"{kind}:{d}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--chunk", type=int, default=40)
    a = ap.parse_args()

    p = subprocess.run([str(devcheck.DEV_PY), "-c", "import duckdb;print(','.join(r[0] for r in duckdb.connect()"
                        ".execute('select name from duckdb_optimizers()').fetchall()))"],
                       capture_output=True, text=True, timeout=60)
    noopt = "SET disabled_optimizers='" + p.stdout.strip().splitlines()[-1] + "'"
    rng = random.Random(a.seed)
    qs = list(dict.fromkeys(build(rng) for _ in range(a.n)))
    cases = [{"id": i, "q": q} for i, q in enumerate(qs)]
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"DuckDB {devcheck.version()}: {len(cases)} distinct queries", flush=True)
    seen: Counter = Counter()
    t0 = time.time()
    with open(OUT / "hits.jsonl", "w") as fh:
        for i in range(0, len(cases), a.chunk):
            for f in run_chunk(cases[i:i + a.chunk], noopt):
                s = sig(f["kind"], f["detail"])
                seen[s] += 1
                if seen[s] == 1:
                    f["sql"] = next((c["q"] for c in cases if c["id"] == f["id"]), None)
                    fh.write(json.dumps(f) + "\n")
                    fh.flush()
                    print(f"[{f['id']}] {f['kind']:<10} {f['detail'][:110]}", flush=True)
            if (i // a.chunk) % 10 == 9:
                print(f"... {i + a.chunk}/{len(cases)} {dict(Counter(k.split(':')[0] for k in seen))} {time.time() - t0:.0f}s", flush=True)
    print(f"done in {time.time() - t0:.0f}s: {len(seen)} unique signatures", flush=True)


if __name__ == "__main__":
    main()
