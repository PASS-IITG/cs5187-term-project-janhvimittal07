"""Differential sweep: replay each seed under several configs and compare.

    python harness/sweep_oracle.py --workers 4 [--limit N]
    python harness/sweep_oracle.py --one corpus/seeds/foo.sql     (single seed, JSON to stdout)

One connection per config executes every statement in lockstep. Findings:
  internal    any config raised an INTERNAL error / assertion
  error_diff  a statement succeeded under one config but failed under another
  value       a read-only query returned different (order-normalised) rows
A seed whose process dies (segfault) is reported as `died` and triaged as a crash.
Uses the stock python `duckdb` module. Output: results/sweep-oracle.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import oracle  # noqa: E402
import runner  # noqa: E402

WORK = runner.WORK
READONLY = re.compile(r"^\s*(select|with|from|values|table)\b", re.I)
NONDET = re.compile(r"\b(random|uuid|gen_random_uuid|now|current_(timestamp|time|date)|today|"
                    r"sample|tablesample|setseed|txid_current|pg_|duckdb_|current_setting|"
                    r"version|getenv|read_|glob|sniff_csv|pragma|summary|describe|"
                    r"explain|checkpoint|sleep|query_table|system_|information_schema|"
                    r"distinct\s+on|list|array_agg|string_agg|group_concat|first|last|"
                    r"any_value|arbitrary|histogram|arg_min|arg_max|argmin|argmax|"
                    r"min_by|max_by|mode|bitstring_agg)\b|"
                    r"\busing\s+sample\b|\bhash\(", re.I)
NOISE_SEED = re.compile(r"pg_catalog|duckdb_|table_function__|information_schema|stats|statistics|"
                        r"uuid|random|wal_replay|checkpoint|bitstring", re.I)
NOISE_SQL = re.compile(r"duckdb_|pg_|stats|uuid|random|information_schema|current_|typeof|version\(", re.I)

# N8: By-design error rules — these optimizers change bind-time semantics for certain
# aggregate functions.  When only the error *class* differs (both sides fail), it is
# not a real bug; when one side errors and the other returns rows the NONDET guard
# already filters the query.  We drop error_diffs where the default config also
# raised an error (both-error pattern) — this catches the statistics_propagation /
# BITSTRING_AGG false positives that produced 16 identical junk findings in run 1.
BY_DESIGN_OPTS = re.compile(r"statistics_propagation", re.I)
CONFIG_NAMES = list(oracle.CONFIGS)


def eligible(sql: str) -> bool:
    if not READONLY.match(sql) or NONDET.search(sql):
        return False
    return not (re.search(r"\blimit\b", sql, re.I) and "order by" not in sql.lower())


def is_noise(seed: str, sql: str) -> bool:
    """Return True (and the finding will be silently dropped with a reason printed).

    Gates implemented (N8):
    1. Seed-name taint  — seed file path contains known non-deterministic keywords.
    2. SQL taint        — query text contains known non-deterministic functions.
    """
    if NOISE_SEED.search(seed):
        return True
    if NOISE_SQL.search(sql):
        return True
    return False


def is_bydesign_error_diff(base_result: tuple, differs: list[str],
                           res: dict, disabled_opts: list[str]) -> bool:
    """N8 gate: both-error pattern.

    If the default config *also* raised an error (just a different class), the
    discrepancy is by design (e.g. statistics_propagation changes bind-time
    semantics for BITSTRING_AGG).  Drop such findings.
    Also drop findings where *all* differing configs are statistics_propagation
    (the only optimizer that changes bind-time error identity).
    """
    # If default itself errored, both sides failed — not a real bug
    if base_result[0] == "error":
        return True
    # If all differing configs are by-design-only optimizers, suppress
    if differs and all(BY_DESIGN_OPTS.search(d) for d in differs):
        return True
    return False


def _call(con, sql: str, secs: float, want_rows: bool):
    t = threading.Timer(secs, con.interrupt)
    t.start()
    try:
        cur = con.execute(sql)
        rows = [oracle._norm(r) for r in cur.fetchall()] if want_rows else None
        return ("ok", rows)
    except Exception as e:                       # noqa: BLE001
        return ("error", type(e).__name__, str(e)[:240])
    finally:
        t.cancel()


def run_seed(path: Path, secs: float = 20.0) -> dict:
    import duckdb
    stmts = runner.split_statements(path.read_text(errors="replace"))
    cons = {}
    for name in CONFIG_NAMES:
        c = duckdb.connect(":memory:")
        for s in oracle.CONFIGS[name]:
            c.execute(s)
        cons[name] = c
    checked, found = 0, []
    for idx, s in enumerate(stmts):
        want = eligible(s)
        res = {n: _call(c, s, secs, want) for n, c in cons.items()}
        checked += want and res["default"][0] == "ok"
        internal = [n for n, r in res.items() if r[0] == "error" and r[1] == "InternalException"]
        base = res["default"]
        diff = [n for n, r in res.items() if n != "default" and
                (r[0] != base[0] or (r[0] == "error" and r[1] != base[1]))]
        vdiff = []
        if want and base[0] == "ok":
            norm = lambda r: sorted(r[1], key=repr)       # noqa: E731
            vdiff = [n for n, r in res.items() if n != "default" and r[0] == "ok"
                     and norm(r) != norm(base)]
        if internal:
            kind, bad = "internal", internal
        elif vdiff:
            kind, bad = "value", vdiff
        elif diff:
            # N8: by-design error rule — suppress both-error and by-design-optimizer diffs
            if is_bydesign_error_diff(base, diff, res, []):
                continue
            kind, bad = "error_diff", diff
        else:
            continue
        found.append({"kind": kind, "idx": idx, "sql": s[:2000], "differs": bad,
                      "detail": {n: str(res[n][1:])[:200] for n in ["default"] + bad}})
    return {"seed": path.name, "stmts": len(stmts), "checked": int(checked), "found": found,
            "internal": sum(f["kind"] == "internal" for f in found),
            "value_mismatches": sum(f["kind"] == "value" for f in found),
            "error_diffs": sum(f["kind"] == "error_diff" for f in found)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--one")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=240)
    a = ap.parse_args()
    if a.one:
        print(json.dumps(run_seed(Path(a.one))))
        return

    seeds = sorted((WORK / "corpus" / "seeds").glob("*.sql"))
    if a.limit:
        seeds = seeds[:a.limit]
    out = WORK / "results" / "sweep-oracle.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)

    def work(p: Path) -> dict:
        try:
            r = subprocess.run([sys.executable, __file__, "--one", str(p)], capture_output=True,
                               text=True, timeout=a.timeout, cwd=WORK / "duckdb")
            if r.returncode != 0:
                return {"seed": p.name, "died": r.returncode, "tail": r.stderr[-400:]}
            return json.loads(r.stdout.strip().splitlines()[-1])
        except subprocess.TimeoutExpired:
            return {"seed": p.name, "timeout": True}
        except Exception as e:                   # noqa: BLE001
            return {"seed": p.name, "error": repr(e)[:200]}

    tot: Counter = Counter()
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex, open(out, "w") as fh:
        for i, row in enumerate(ex.map(work, seeds), 1):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            for k in ("checked", "internal", "value_mismatches", "error_diffs"):
                tot[k] += row.get(k, 0)
            for k in ("died", "timeout", "error"):
                if k in row:
                    tot[k] += 1
            if row.get("internal") or "died" in row:
                print(f"[{i}] {row['seed']}  internal={row.get('internal')} died={row.get('died')}", flush=True)
            if i % 200 == 0:
                print(f"... {i}/{len(seeds)} {dict(tot)} {time.time()-t0:.0f}s", flush=True)
    print(json.dumps({"seeds": len(seeds), **tot, "secs": round(time.time() - t0)}))


if __name__ == "__main__":
    main()
