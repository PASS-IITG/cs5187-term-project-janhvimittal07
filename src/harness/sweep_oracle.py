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
# aggregate functions. When only the error *class* differs (both sides fail), it is
# not a real bug; when one side errors and the other returns rows the NONDET guard
# already filters the query. We drop error_diffs where the default config also
# raised an error (both-error pattern) or where BITSTRING_AGG / extract is involved.
BY_DESIGN_OPTS = re.compile(r"statistics_propagation", re.I)
SETUP_TAINT_RX = re.compile(
    r"\b(random|uuid|gen_random_uuid|now|current_(timestamp|time|date)|today|"
    r"txid_current|setseed|tablesample)\b|\busing\s+sample\b", re.I
)
CONFIG_NAMES = list(oracle.CONFIGS)


def is_unordered_window(sql: str) -> bool:
    """N8 gate: Unordered-window filter — window frame without an explicit ORDER BY."""
    for m in re.finditer(r"\bover\s*\(([^)]+)\)", sql, re.I):
        clause = m.group(1).lower()
        if any(w in clause for w in ("rows", "range", "groups")) and "order by" not in clause:
            return True
    return False


def eligible(sql: str) -> bool:
    if not READONLY.match(sql) or NONDET.search(sql):
        return False
    if is_unordered_window(sql):
        return False
    return not (re.search(r"\blimit\b", sql, re.I) and "order by" not in sql.lower())


def is_noise(seed: str, sql: str) -> bool:
    """Return True if seed or query is noise (N8 gates).

    Gates implemented:
    1. Seed-name taint  — seed file path contains known non-deterministic keywords.
    2. SQL taint        — query text contains known non-deterministic functions.
    3. Unordered window — window functions without ORDER BY in window frame.
    4. By-design agg    — bitstring_agg requiring statistics_propagation.
    """
    if NOISE_SEED.search(seed):
        return True
    if NOISE_SQL.search(sql):
        return True
    if is_unordered_window(sql):
        return True
    if re.search(r"\bbitstring_agg\b", sql, re.I):
        return True
    return False


def is_bydesign_error_diff(base_result: tuple, differs: list[str],
                           res: dict, sql: str = "") -> bool:
    """N8 gate: By-design error rules.

    1. If the default config *also* raised an error (just a different class), both
       sides rejected the query — not a bug.
    2. BITSTRING_AGG requires statistics_propagation to determine bitbounds. If that
       pass is disabled (or no_optimizers is used), a bind-time error is by design.
    3. EXTRACT on time / empty tables differences.
    4. If all differing configs are by-design optimizers (statistics_propagation).
    """
    # If default itself errored, both sides failed
    if base_result[0] == "error":
        return True
    # BITSTRING_AGG requires statistics_propagation
    if re.search(r"\bbitstring_agg\b", sql, re.I):
        if any("statistics_propagation" in d or "no_optimizers" in d for d in differs):
            return True
    # EXTRACT bind error vs runtime
    if re.search(r"\bextract\b", sql, re.I) and any("no_optimizers" in d for d in differs):
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


def run_seed(path: Path, secs: float = 20.0,
             check_norec: bool = False, check_tlp: bool = False,
             metamorphic_limit: int = 5) -> dict:
    import duckdb
    stmts = runner.split_statements(path.read_text(errors="replace"))
    cons = {}
    for name in CONFIG_NAMES:
        c = duckdb.connect(":memory:")
        for s in oracle.CONFIGS[name]:
            c.execute(s)
        cons[name] = c
    checked, found = 0, []
    setup_tainted = False
    for idx, s in enumerate(stmts):
        if not READONLY.match(s) and SETUP_TAINT_RX.search(s):
            setup_tainted = True
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
            # N8: Setup taint tracking — tainted setups cannot produce valid value differences
            if setup_tainted:
                continue
            kind, bad = "value", vdiff
        elif diff:
            # N8: by-design error rule — suppress both-error and by-design-optimizer diffs
            if is_bydesign_error_diff(base, diff, res, s):
                continue
            kind, bad = "error_diff", diff
        else:
            continue
        found.append({"kind": kind, "idx": idx, "sql": s[:2000], "differs": bad,
                      "detail": {n: str(res[n][1:])[:200] for n in ["default"] + bad}})

    # N5: Metamorphic Oracles (NoREC & TLP) over tables created by the seed
    if (check_norec or check_tlp) and not setup_tainted:
        c_default = cons.get("default")
        if c_default:
            try:
                schema = oracle.extract_schema(c_default)
                for tname, cols in schema.items():
                    try:
                        c_default.execute(f'SELECT 1 FROM "{tname}" LIMIT 1')
                    except Exception:
                        continue
                    preds = oracle.generate_predicates(cols, max_predicates=metamorphic_limit)
                    for pred in preds:
                        if check_norec:
                            for opt_q, ref_q, agg_desc in oracle.generate_norec_pairs(tname, pred, cols):
                                r_opt = _call(c_default, opt_q, secs, True)
                                r_ref = _call(c_default, ref_q, secs, True)
                                checked += 1
                                if r_opt[0] == "ok" and r_ref[0] == "ok":
                                    if r_opt[1] != r_ref[1]:
                                        found.append({
                                            "kind": "norec",
                                            "idx": len(stmts),
                                            "sql": f"{opt_q}  /* vs */  {ref_q}",
                                            "differs": ["norec_mismatch"],
                                            "detail": {"opt": r_opt[1], "ref": r_ref[1], "predicate": pred, "agg": agg_desc}
                                        })
                                elif r_opt[0] != r_ref[0]:
                                    found.append({
                                        "kind": "norec",
                                        "idx": len(stmts),
                                        "sql": f"{opt_q}  /* vs */  {ref_q}",
                                        "differs": ["norec_error_diff"],
                                        "detail": {"opt": r_opt, "ref": r_ref, "predicate": pred, "agg": agg_desc}
                                    })
                        if check_tlp:
                            whole_q, parts_q = oracle.generate_tlp_pairs(f'"{tname}"', pred, mode="count")
                            r_whole = _call(c_default, whole_q, secs, True)
                            r_parts = _call(c_default, parts_q, secs, True)
                            checked += 1
                            if r_whole[0] == "ok" and r_parts[0] == "ok":
                                if r_whole[1] != r_parts[1]:
                                    found.append({
                                        "kind": "tlp",
                                        "idx": len(stmts),
                                        "sql": f"{whole_q}  /* vs */  {parts_q}",
                                        "differs": ["tlp_mismatch"],
                                        "detail": {"whole": r_whole[1], "parts": r_parts[1], "predicate": pred}
                                    })
                            elif r_whole[0] != r_parts[0] and any(
                                    r[0] == "error" and (r[1] == "InternalException" or "INTERNAL" in r[2])
                                    for r in (r_whole, r_parts)):
                                # whole query never evaluates the predicate: only INTERNAL errors are bugs
                                found.append({
                                    "kind": "tlp",
                                    "idx": len(stmts),
                                    "sql": f"{whole_q}  /* vs */  {parts_q}",
                                    "differs": ["tlp_error_diff"],
                                    "detail": {"whole": r_whole, "parts": r_parts, "predicate": pred}
                                })
            except Exception:
                pass

    return {"seed": path.name, "stmts": len(stmts), "checked": int(checked), "found": found,
            "internal": sum(f["kind"] == "internal" for f in found),
            "value_mismatches": sum(f["kind"] == "value" for f in found),
            "error_diffs": sum(f["kind"] == "error_diff" for f in found),
            "norec_mismatches": sum(f["kind"] == "norec" for f in found),
            "tlp_mismatches": sum(f["kind"] == "tlp" for f in found)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--one")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=240)
    ap.add_argument("--norec", action="store_true", help="Run NoREC metamorphic checks on seed tables")
    ap.add_argument("--tlp", action="store_true", help="Run TLP metamorphic checks on seed tables")
    ap.add_argument("--metamorphic", action="store_true", help="Run both NoREC and TLP checks")
    ap.add_argument("--metamorphic-limit", type=int, default=5, help="Max generated predicates per table")
    a = ap.parse_args()
    if a.one:
        print(json.dumps(run_seed(
            Path(a.one),
            check_norec=a.norec or a.metamorphic,
            check_tlp=a.tlp or a.metamorphic,
            metamorphic_limit=a.metamorphic_limit,
        )))
        return

    seeds = sorted((WORK / "corpus" / "seeds").glob("*.sql"))
    if a.limit:
        seeds = seeds[:a.limit]
    out = WORK / "results" / "sweep-oracle.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)

    def work(p: Path) -> dict:
        cmd = [sys.executable, __file__, "--one", str(p)]
        if a.norec:
            cmd.append("--norec")
        if a.tlp:
            cmd.append("--tlp")
        if a.metamorphic:
            cmd.append("--metamorphic")
        if a.metamorphic_limit != 5:
            cmd.extend(["--metamorphic-limit", str(a.metamorphic_limit)])
        try:
            r = subprocess.run(cmd, capture_output=True,
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
            for k in ("checked", "internal", "value_mismatches", "error_diffs", "norec_mismatches", "tlp_mismatches"):
                tot[k] += row.get(k, 0)
            for k in ("died", "timeout", "error"):
                if k in row:
                    tot[k] += 1
            if row.get("internal") or "died" in row or row.get("norec_mismatches") or row.get("tlp_mismatches"):
                print(f"[{i}] {row['seed']}  internal={row.get('internal')} died={row.get('died')} "
                      f"norec={row.get('norec_mismatches')} tlp={row.get('tlp_mismatches')}", flush=True)
            if i % 200 == 0:
                print(f"... {i}/{len(seeds)} {dict(tot)} {time.time()-t0:.0f}s", flush=True)
    print(json.dumps({"seeds": len(seeds), **tot, "secs": round(time.time() - t0)}))


if __name__ == "__main__":
    main()
