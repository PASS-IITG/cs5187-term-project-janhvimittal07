"""Trigger fuzzer for DuckDB 2.0 (triggers are new: CREATE TRIGGER, REFERENCING transition tables, FOR EACH ROW).

    python harness/fuzz_triggers.py --n 4000 --seed 1

Nobody has reported a trigger bug since 2.0.0 shipped (the open issue stream is all joins / subqueries / storage), and
DuckDB's own tests only cover hand-written cases. Each case = a schema, 1-3 random triggers, and 3-8 random statements
(INSERT / UPDATE / DELETE / MERGE / INSERT ON CONFLICT, some with RETURNING, some in transactions that roll back, some
ALTER / DROP TABLE of trigger-bearing tables). Oracles:

  emu      for cases whose triggers only log into side tables: re-run every DML WITHOUT triggers and apply the trigger
           bodies by hand (transition tables built from RETURNING / the pre-image, NEW./OLD. substituted per row). The
           final contents of every table and every statement's success must match the trigger run.
  persist  the same statement list on a file database that is closed and reopened (with and without CHECKPOINT) must give
           the same results as in memory (CREATE/DROP TRIGGER are WAL- and catalog-serialised).
  detect   INTERNAL / FATAL errors, crashes and hangs, run under PRAGMA enable_verification.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import devcheck  # noqa: E402
import runner  # noqa: E402

WORK = runner.WORK
OUT = WORK / "results" / "triggers"

AVOID_KNOWN = True      # see build(): skip the cascade pattern of duckdb/duckdb#27759
TABLE_VARIANTS = [
    "id INTEGER, val VARCHAR, n INTEGER",
    "id INTEGER PRIMARY KEY, val VARCHAR, n INTEGER",
    "id INTEGER, val VARCHAR, n INTEGER DEFAULT 5",
    "id INTEGER, val VARCHAR NOT NULL, n INTEGER CHECK (n IS NULL OR n >= 0)",
    "id INTEGER, val VARCHAR, n INTEGER, g INTEGER GENERATED ALWAYS AS (n * 2)",
    "id INTEGER, g INTEGER GENERATED ALWAYS AS (id + 1), val VARCHAR, n INTEGER",
]
VALS = ["'a'", "'b'", "'c'", "''", "NULL"]
NS = ["0", "1", "2", "10", "NULL", "-1"]
IDS = ["1", "2", "3", "4", "NULL"]


def cols_of(variant: str) -> list[str]:
    """Insertable column names (generated columns excluded)."""
    out = []
    for part in variant.split(","):
        name = part.strip().split()[0]
        if "GENERATED" not in part and name not in ("PRIMARY",):
            out.append(name)
    return out


def row(rng: random.Random, cols: list[str]) -> str:
    gen = {"id": rng.choice(IDS), "val": rng.choice(VALS), "n": rng.choice(NS)}
    return "(" + ", ".join(gen[c] for c in cols) + ")"


def build(rng: random.Random) -> dict:
    tv = [rng.choice(TABLE_VARIANTS), rng.choice(TABLE_VARIANTS)]
    cols = [cols_of(v) for v in tv]
    schema = [f"CREATE TABLE t({tv[0]})", f"CREATE TABLE u({tv[1]})",
              "CREATE TABLE l1(tag VARCHAR, id INTEGER, val VARCHAR, n INTEGER)",
              "CREATE TABLE l2(tag VARCHAR, id INTEGER, val VARCHAR, n INTEGER)"]
    # seed rows
    for tn, cs in (("t", cols[0]), ("u", cols[1])):
        for _ in range(rng.randint(0, 3)):
            schema.append(f"INSERT INTO {tn} ({', '.join(cs)}) VALUES {row(rng, cs)}")
    triggers = []
    emu_ok = True
    # DuckDB 2.0.0 rejects: BEFORE / UPDATE row triggers, mixing statement and row triggers on one table, and row
    # triggers on tables with generated columns - so pick one level per table and only generate what can be created.
    level_of = {tn: ("ROW" if rng.random() < 0.3 and "GENERATED" not in tv[k] else "STATEMENT") for k, tn in enumerate(("t", "u"))}
    for i in range(rng.randint(1, 3)):
        tbl = rng.choice(["t", "t", "u"])
        level = level_of[tbl]
        event = rng.choice(["INSERT", "DELETE"] if level == "ROW" else ["INSERT", "UPDATE", "DELETE"])
        timing = "AFTER" if level == "ROW" else rng.choice(["AFTER", "AFTER", "BEFORE"])
        log = rng.choice(["l1", "l2"])
        ref, body = "", ""
        upd_of = ""
        if event == "UPDATE" and rng.random() < 0.3:
            upd_of = " OF " + rng.choice(["n", "val", "id"])
        shape = rng.random()
        if level == "STATEMENT":
            if timing == "AFTER" and shape < 0.5:
                if event == "INSERT":
                    ref, body = "REFERENCING NEW TABLE AS nw", f"INSERT INTO {log} SELECT 'i{i}', id, val, n FROM nw"
                elif event == "DELETE":
                    ref, body = "REFERENCING OLD TABLE AS od", f"INSERT INTO {log} SELECT 'd{i}', id, val, n FROM od"
                elif not upd_of:
                    ref = rng.choice(["REFERENCING OLD TABLE AS od", "REFERENCING NEW TABLE AS nw", "REFERENCING OLD TABLE AS od NEW TABLE AS nw"])
                    if "od" in ref and "nw" in ref:
                        body = f"INSERT INTO {log} SELECT 'u{i}', nw.id, od.val, nw.n FROM od JOIN nw ON od.id = nw.id"
                    elif "od" in ref:
                        body = f"INSERT INTO {log} SELECT 'uo{i}', id, val, n FROM od"
                    else:
                        body = f"INSERT INTO {log} SELECT 'un{i}', id, val, n FROM nw"
            if not body:
                body = rng.choice([f"INSERT INTO {log} VALUES ('s{i}', 0, 'x', 0)",
                                   f"INSERT INTO {log} SELECT 'c{i}', count(*)::INTEGER, NULL, sum(n)::INTEGER FROM {tbl}",
                                   f"INSERT INTO {log} SELECT 'u{i}', id, val, n FROM u"])
        else:
            pre = "NEW" if event == "INSERT" else "OLD"
            body = f"INSERT INTO {log} VALUES ('r{i}', {pre}.id, {pre}.val, {pre}.n)"
        if rng.random() < 0.12:                      # cascade: the body writes a table that has triggers of its own
            other = "u" if tbl == "t" else "t"
            ocols = cols[1 if tbl == "t" else 0]
            if level == "ROW":
                pre = "NEW" if event == "INSERT" else "OLD"
                vals = ", ".join(f"{pre}.{c}" if c in ("id", "val", "n") else "NULL" for c in ocols)
                body = f"INSERT INTO {other} ({', '.join(ocols)}) VALUES ({vals})"
            else:
                body = f"INSERT INTO {other} ({', '.join(ocols)}) VALUES {row(rng, ocols)}"
            emu_ok = False
        trg = f"CREATE TRIGGER trg{i} {timing} {event}{upd_of} ON {tbl} {ref} FOR EACH {level} {body}".replace("  ", " ")
        triggers.append({"name": f"trg{i}", "tbl": tbl, "timing": timing, "event": event, "level": level,
                         "ref": ref, "body": body, "sql": trg, "of": upd_of.replace(" OF ", "").strip()})
    # #27759: a ROW trigger that cascades into a table with a BEFORE INSERT statement trigger raises an INTERNAL error;
    # drop such BEFORE triggers (unless --keep-known) so the run explores everything else
    if AVOID_KNOWN:
        row_targets = set()
        for tg in triggers:
            m = re.match(r"INSERT INTO (\w+)", tg["body"])
            if tg["level"] == "ROW" and m and m.group(1) in ("t", "u") and m.group(1) != tg["tbl"]:
                row_targets.add(m.group(1))
        triggers = [tg for tg in triggers if not (tg["timing"] == "BEFORE" and tg["event"] == "INSERT" and tg["tbl"] in row_targets)]
        if not triggers:
            triggers = [{"name": "trg0", "tbl": "t", "timing": "AFTER", "event": "INSERT", "level": "STATEMENT", "ref": "",
                         "body": "INSERT INTO l1 VALUES ('s', 0, 'x', 0)", "sql": "CREATE TRIGGER trg0 AFTER INSERT ON t FOR EACH STATEMENT INSERT INTO l1 VALUES ('s', 0, 'x', 0)", "of": ""}]
    steps = []
    for _ in range(rng.randint(3, 8)):
        tn = rng.choice(["t", "t", "u"])
        cs = cols[0 if tn == "t" else 1]
        r = rng.random()
        other = "u" if tn == "t" else "t"
        ret = " RETURNING *" if rng.random() < 0.25 and level_of[tn] != "ROW" else ""
        if r < 0.28:
            rows = ", ".join(row(rng, cs) for _ in range(rng.randint(1, 3)))
            sql, kind = f"INSERT INTO {tn} ({', '.join(cs)}) VALUES {rows}{ret}", "insert"
        elif r < 0.38:
            sql, kind = f"INSERT INTO {tn} ({', '.join(cs)}) SELECT {', '.join(cs)} FROM {other}{ret}", "insert"
        elif r < 0.52:
            sql, kind = f"UPDATE {tn} SET n = {rng.choice(['n + 1', '5', 'NULL', 'id'])} WHERE {rng.choice(['id = 1', 'id IS NULL', 'true', 'n > 0', 'val = ' + rng.choice(VALS[:3])])}{ret}", "update"
        elif r < 0.60:
            sql, kind = f"UPDATE {tn} SET val = {rng.choice(VALS)} WHERE id = {rng.choice(IDS[:3])}{ret}", "update"
        elif r < 0.72:
            sql, kind = f"DELETE FROM {tn} WHERE {rng.choice(['id = 1', 'id IS NULL', 'true', 'n < 2', 'val IS NULL'])}{ret}", "delete"
        elif r < 0.80:
            sql, kind = f"INSERT INTO {tn} ({', '.join(cs)}) VALUES {row(rng, cs)} ON CONFLICT DO NOTHING{ret}", "other"
        elif r < 0.88:
            sql, kind = (f"MERGE INTO {tn} USING {other} s ON {tn}.id = s.id WHEN MATCHED THEN UPDATE SET n = s.n "
                         f"WHEN NOT MATCHED THEN INSERT ({', '.join(cs)}) VALUES ({', '.join('s.' + c for c in cs)})"), "other"
        elif r < 0.92:
            sql, kind = rng.choice([f"ALTER TABLE {tn} RENAME TO {tn}_r", f"ALTER TABLE {tn} ADD COLUMN extra INTEGER",
                                    f"ALTER TABLE {tn} DROP COLUMN n", f"DROP TABLE {tn}", "DROP TABLE l1", "DROP TABLE l2",
                                    f"DROP TRIGGER trg{rng.randint(0, 2)} ON {tn}", "CHECKPOINT"]), "ddl"
        elif r < 0.96:
            sql, kind = f"INSERT OR REPLACE INTO {tn} ({', '.join(cs)}) VALUES {row(rng, cs)}", "other"
        else:
            sql, kind = f"TRUNCATE {tn}" if rng.random() < 0.5 else f"DELETE FROM {tn}", "other"
        if kind in ("other", "ddl"):
            emu_ok = False
        steps.append({"sql": sql, "kind": kind, "tbl": tn, "txn": rng.random() < 0.15})
    return {"schema": schema, "triggers": triggers, "steps": steps, "emu": emu_ok}


CHILD = r'''
import duckdb, json, sys, os, re, tempfile, shutil

d = json.load(sys.stdin)

def lit(v):
    if v is None: return "NULL"
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, int): return str(v)
    return "'" + str(v).replace("'", "''") + "'"

def classify(e):
    m = str(e); n = type(e).__name__
    if n == "FatalException" or "FATAL Error" in m: return "fatal"
    if n == "InternalException" or "INTERNAL Error" in m: return "internal"
    return "err"

def setup(con, verify):
    con.execute("SET max_execution_time=20000"); con.execute("SET memory_limit='1GB'"); con.execute("SET threads=2")
    if verify: con.execute("PRAGMA enable_verification")

def emit(o): print(json.dumps(o), flush=True)

def tables(con):
    out = {}
    for (n,) in con.execute("select table_name from duckdb_tables() where not internal order by 1").fetchall():
        try:
            out[n] = sorted(repr(r) for r in con.execute(f'SELECT * FROM "{n}"').fetchall())
        except Exception as e:
            out[n] = "ERR " + type(e).__name__
    return out

def run_stmt(con, sql, in_txn):
    """Return (status, rows) for one statement; rows only for RETURNING."""
    try:
        cur = con.execute(sql)
        rows = sorted(repr(r) for r in cur.fetchall()) if cur.description and "RETURNING" in sql.upper() else None
        return ("ok", rows)
    except Exception as e:
        k = classify(e)
        return (k, type(e).__name__ if k == "err" else str(e)[:160])

def run_case(case, con, reopen=None, verify=True):
    """Run schema + triggers + steps; returns (statuses, final tables). Reopen() gives a fresh connection (persist mode)."""
    res = []
    for s in case["schema"]:
        try: con.execute(s)
        except Exception: pass
    for t in case["triggers"]:
        res.append(("trigger",) + run_stmt(con, t["sql"], False))
    for i, st in enumerate(case["steps"]):
        if reopen and i % 2 == 1:
            con = reopen(con)
        if st["txn"]:
            try: con.execute("BEGIN")
            except Exception: pass
        r = run_stmt(con, st["sql"], st["txn"])
        res.append(r)
        if st["txn"]:
            try: con.execute("ROLLBACK" if i % 2 == 0 else "COMMIT")
            except Exception: pass
    return res, tables(con), con

def emulate(case, con):
    """Same statements WITHOUT triggers; trigger bodies applied by hand (only for emu-eligible cases)."""
    trs = case["triggers"]
    res = []
    for s in case["schema"]:
        try: con.execute(s)
        except Exception: pass
    for t in trs:
        res.append(("trigger", "ok", None))
    for i, st in enumerate(case["steps"]):
        tn, kind, sql = st["tbl"], st["kind"], st["sql"]
        mine = [t for t in trs if t["tbl"] == tn and t["event"] == kind.upper()]
        if kind == "update":
            m_set = re.search(r"\bSET (.*?) WHERE", sql, re.S)
            assigned = set(re.findall(r"(\w+)\s*=", m_set.group(1))) if m_set else set()
            mine = [t for t in mine if not t["of"] or t["of"] in assigned]
        if st["txn"]:
            try: con.execute("BEGIN")
            except Exception: pass
        try:
            con.execute("SAVEPOINT_DUMMY") if False else None
            ok, rows_out, err = True, None, None
            try: con.execute("BEGIN" if not st["txn"] else "SELECT 1")
            except Exception: pass
            # pre-image
            old = None
            if kind in ("update", "delete"):
                where = re.search(r"\bWHERE (.*?)(?: RETURNING \*)?$", sql, re.S)
                cur = con.execute(f"SELECT id, val, n FROM {tn}" + (f" WHERE {where.group(1)}" if where else ""))
                old = cur.fetchall()
            before = [t for t in mine if t["timing"] == "BEFORE" and t["level"] == "STATEMENT"]
            for t in before:
                con.execute(t["body"])
            dml = sql if "RETURNING" in sql.upper() else sql + " RETURNING id, val, n"
            cur = con.execute(dml)
            got = cur.fetchall()
            ret_cols = [c[0] for c in cur.description]
            rows_out = sorted(repr(r) for r in got) if "RETURNING" in sql.upper() else None
            nw = [(r[ret_cols.index("id")], r[ret_cols.index("val")], r[ret_cols.index("n")]) for r in got] if {"id", "val", "n"} <= set(ret_cols) else None
            after = [t for t in mine if t["timing"] == "AFTER"]
            for t in after:
                if t["level"] == "STATEMENT":
                    m = re.search(r"REFERENCING (OLD TABLE AS (\w+))?\s*(NEW TABLE AS (\w+))?", t["ref"]) if t["ref"] else None
                    for alias, data in ((m.group(2), old) if m and m.group(2) else (None, None), (m.group(4), nw) if m and m.group(4) else (None, None)):
                        if alias:
                            con.execute(f"CREATE OR REPLACE TEMP TABLE {alias}(id INTEGER, val VARCHAR, n INTEGER)")
                            for r in (data or []): con.execute(f"INSERT INTO {alias} VALUES ({', '.join(lit(x) for x in r)})")
                    con.execute(t["body"])
                    for alias in [m.group(2), m.group(4)] if m else []:
                        if alias: con.execute(f"DROP TABLE IF EXISTS {alias}")
                else:
                    if kind == "insert": rowsets = [(None, r) for r in (nw or [])]
                    elif kind == "delete": rowsets = [(r, None) for r in (old or [])]
                    else: rowsets = list(zip(old or [], nw or []))
                    for o, n_ in rowsets:
                        b = t["body"]
                        for pre, src in (("OLD", o), ("NEW", n_)):
                            if src is not None:
                                for ci, cn in enumerate(("id", "val", "n")):
                                    b = b.replace(f"{pre}.{cn}", lit(src[ci]))
                        con.execute(b)
            if not st["txn"]: con.execute("COMMIT")
            res.append(("ok", rows_out))
        except Exception as e:
            try: con.execute("ROLLBACK")
            except Exception: pass
            k = classify(e)
            res.append((k, type(e).__name__ if k == "err" else str(e)[:160]))
        if st["txn"]:
            try: con.execute("ROLLBACK" if i % 2 == 0 else "COMMIT")
            except Exception: pass
    return res, tables(con)

STAT = {"cases": 0, "emu_eligible": 0, "emu_checked": 0, "emu_exception": 0, "persist_checked": 0, "triggers_created": 0, "trigger_fired_rows": 0}
for case in d["cases"]:
    emit({"at": case["id"]})
    STAT["cases"] += 1
    verify = d["verify"]
    # ---- plain run (in memory) --------------------------------------------------------
    con = duckdb.connect(":memory:"); setup(con, verify)
    base_res, base_tabs, con = run_case(case, con)
    STAT["triggers_created"] += sum(1 for r in base_res if r[0] == "trigger" and r[1] == "ok")
    STAT["trigger_fired_rows"] += sum(len(v) for k, v in base_tabs.items() if k in ("l1", "l2") and isinstance(v, list))
    for r in base_res:
        if r[0] in ("internal", "fatal"):
            emit({"id": case["id"], "kind": r[0], "variant": "run", "detail": str(r[1])[:200]})
    con.close()
    # ---- persist: file DB, reopened between statements ----------------------------------
    tmp = tempfile.mkdtemp(prefix="trg_")
    try:
        path = os.path.join(tmp, "db.duckdb")
        pcon = duckdb.connect(path); setup(pcon, verify)
        def reopen(c, path=path):
            try: c.execute("CHECKPOINT") if case["id"] % 2 == 0 else None
            except Exception: pass
            c.close(); n = duckdb.connect(path); setup(n, verify); return n
        p_res, p_tabs, pcon = run_case(case, pcon, reopen=reopen)
        for r in p_res:
            if r[0] in ("internal", "fatal"):
                emit({"id": case["id"], "kind": r[0], "variant": "persist", "detail": str(r[1])[:200]})
        pcon.close()
        STAT["persist_checked"] += 1
        if [r[:2] for r in base_res] != [r[:2] for r in p_res] or base_tabs != p_tabs:
            emit({"id": case["id"], "kind": "persist_mismatch", "variant": "persist",
                  "detail": "statuses %s | tables differ: %s" % (
                      "same" if [r[:2] for r in base_res] == [r[:2] for r in p_res] else "DIFFER",
                      [k for k in set(base_tabs) | set(p_tabs) if base_tabs.get(k) != p_tabs.get(k)][:4])})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # ---- emulation oracle ----------------------------------------------------------------
    if case["emu"] and not any(r[0] in ("internal", "fatal") for r in base_res):
        STAT["emu_eligible"] += 1
        con = duckdb.connect(":memory:"); setup(con, verify)
        try:
            e_res, e_tabs = emulate(case, con)
            STAT["emu_checked"] += 1
            # compare statuses of the DML steps only (trigger creation statuses are not emulated)
            n_t = len(case["triggers"])
            a = [r[:2] if r[0] != "err" else ("err", None) for r in base_res[n_t:]]
            b = [r[:2] if r[0] != "err" else ("err", None) for r in e_res[n_t:]]
            if a != b or base_tabs != e_tabs:
                emit({"id": case["id"], "kind": "emu_mismatch", "variant": "emu",
                      "detail": "statuses %s | tables differ: %s" % ("same" if a == b else "DIFFER %s vs %s" % (a, b),
                                                                    [k for k in set(base_tabs) | set(e_tabs) if base_tabs.get(k) != e_tabs.get(k)][:4])})
        except Exception as e:
            STAT["emu_exception"] += 1
            STAT.setdefault("emu_exc_msgs", []).append(str(e)[:100])
        con.close()
emit({"stat": STAT})
emit({"done": True})
'''


STATS: Counter = Counter()
STATS_MSGS: Counter = Counter()


def run_chunk(chunk: list[dict], verify: bool, timeout: float = 240.0) -> list[dict]:
    findings: list[dict] = []
    todo = list(chunk)
    while todo:
        err = ""
        try:
            p = subprocess.run([str(devcheck.DEV_PY), "-c", CHILD], input=json.dumps({"cases": todo, "verify": verify}),
                               capture_output=True, text=True, timeout=timeout, cwd=tempfile.gettempdir())
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
            elif "stat" in o:
                for k, v in o["stat"].items():
                    if isinstance(v, int):
                        STATS[k] += v
                    else:
                        STATS_MSGS.update(v)
            elif "done" in o:
                done = True
            elif "kind" in o:
                findings.append(o)
        if done:
            return findings
        findings.append({"id": last, "kind": "timeout" if hung else "crash", "variant": "run", "detail": f"worker rc={rc} {err}"})
        if last is None:
            return findings
        todo = [c for c in todo if c["id"] > last]
    return findings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--chunk", type=int, default=50)
    ap.add_argument("--show", type=int, default=0, help="print N generated cases and exit")
    ap.add_argument("--keep-known", action="store_true", help="also generate the known #27759 cascade pattern")
    a = ap.parse_args()
    global AVOID_KNOWN
    AVOID_KNOWN = not a.keep_known
    rng = random.Random(a.seed)
    cases = [dict(build(rng), id=i) for i in range(a.n)]
    if a.show:
        for c in cases[:a.show]:
            print(json.dumps({k: c[k] for k in ("schema", "emu")}, indent=None)[:300])
            for t in c["triggers"]:
                print("  ", t["sql"])
            for s in c["steps"]:
                print("  >", ("[txn] " if s["txn"] else "") + s["sql"])
            print()
        return
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"DuckDB {devcheck.version()}: {len(cases)} trigger cases", flush=True)
    seen: Counter = Counter()
    by_id = {c["id"]: c for c in cases}
    t0 = time.time()
    with open(OUT / f"hits-seed{a.seed}.jsonl", "w") as fh:
        for verify in (False, True):
            for i in range(0, len(cases), a.chunk):
                for f in run_chunk(cases[i:i + a.chunk], verify):
                    sig = f"{'verify' if verify else 'plain'}:{f['kind']}:{f.get('variant')}:{re.sub(r'[0-9]+', 'N', f['detail'])[:70]}"
                    seen[sig] += 1
                    if seen[sig] == 1:
                        c = by_id.get(f["id"], {})
                        f.update(verify=verify, case=c)
                        fh.write(json.dumps(f) + "\n")
                        fh.flush()
                        print(f"[{'verify' if verify else 'plain'} {f['id']}] {f['kind']:16} {f['detail'][:120]}", flush=True)
            print(f"... {'verify' if verify else 'plain'} pass done {time.time() - t0:.0f}s", flush=True)
    print("oracle coverage:", dict(STATS), flush=True)
    for m, n in STATS_MSGS.most_common(5):
        print(f"   emulation exception x{n}: {m}", flush=True)
    print(f"done in {time.time() - t0:.0f}s: {len(seen)} unique signatures {dict(Counter(k.split(':')[1] for k in seen))}", flush=True)


if __name__ == "__main__":
    main()
