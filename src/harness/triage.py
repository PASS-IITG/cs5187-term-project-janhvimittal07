"""Automatic triage of fuzzing findings.

Given a failing SQL script (or a differential mismatch) this:
  1. finds the config under which it fails (threads / ordering / optimizers),
  2. minimises the statements (delta debugging),
  3. minimises the set of disabled optimizers,
  4. confirms on the stock release build (python `duckdb`),
  5. searches upstream issues and PRs (and diffs fix PRs) for duplicates,
  6. checks whether the finding reproduces on the pinned DuckDB main CLI
     binary (`build-main/duckdb`) — N2 deliverable.

    python harness/triage.py crash  path/to/script.sql
    python harness/triage.py replay            (internal: stock-build child)

Library: triage_crash(stmts, origin), triage_value(setup, query, kind, differs, origin)
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle  # noqa: E402
import runner  # noqa: E402
import sweep_oracle  # noqa: E402

WORK = runner.WORK
OUT = WORK / "results" / "triage"
CRASHY = {"internal_error", "asan", "crash"}
CFG = {**oracle.CONFIGS, **oracle.PER_OPTIMIZER}
CRASH_ORDER = ["default", "single_thread", "many_threads", "no_insertion_order", "no_optimizers"]
REPO = "duckdb/duckdb"

# N2: pinned DuckDB main CLI binary for "reproduces on main?" check.
MAIN_BIN = WORK / "build-main" / "duckdb"

FEATURES = ["NOT MATCHED BY SOURCE", "WHEN NOT MATCHED", "WHEN MATCHED", "ON CONFLICT", "GROUP BY ALL",
            "ORDER BY ALL", "IS NOT DISTINCT FROM", "GROUPING SETS", "ROWS BETWEEN", "RANGE BETWEEN",
            "ASOF", "MERGE", "PIVOT", "UNPIVOT", "UNNEST", "RECURSIVE", "QUALIFY", "LATERAL",
            "ROLLUP", "CUBE", "GROUPING", "WINDOW", "STRUCT", "MAP", "UNION", "JSON", "COPY",
            "EXPLAIN", "INTERSECT", "EXCEPT", "SAMPLE", "PREPARE", "PARQUET", "CSV", "UPSERT",
            "CONFLICT", "RETURNING", "ENUM", "DECIMAL", "ARRAY", "LIST", "INDEX", "MACRO",
            "SEQUENCE", "ATTACH", "FILTER", "DISTINCT", "DELETE", "UPDATE", "INSERT", "JOIN"]


# ---------------------------------------------------------------- running
def prelude(cfg_stmts: list[str]) -> str:
    return "".join(s.strip().rstrip(";") + ";\n" for s in cfg_stmts)


def disable(opts: list[str]) -> list[str]:
    return [f"SET disabled_optimizers='{','.join(opts)}'"] if opts else []


def cli(stmts: list[str], cfg_stmts: list[str], timeout: float = 60) -> runner.Result:
    return runner.run_sql(prelude(cfg_stmts) + "\n".join(stmts) + "\n", "asan", timeout,
                          cwd=WORK / "duckdb")


def stock_replay(stmts: list[str], pre_a: list[str], pre_b: list[str] | None = None,
                 timeout: float = 90) -> dict:
    """Run on the stock python build in a child process; returns rc + per-side outcome."""
    payload = json.dumps({"stmts": stmts, "pre_a": pre_a, "pre_b": pre_b})
    try:
        p = subprocess.run([sys.executable, __file__, "replay"], input=payload, capture_output=True,
                           text=True, timeout=timeout, cwd=WORK / "duckdb")
    except subprocess.TimeoutExpired:
        return {"rc": None, "timeout": True}
    out = {"rc": p.returncode, "segfault": p.returncode in (-11, 139)}
    try:
        out.update(json.loads(p.stdout.strip().splitlines()[-1]))
    except Exception:                              # noqa: BLE001
        pass
    return out


def _replay_child() -> None:
    import duckdb
    d = json.load(sys.stdin)

    def side(pre):
        c = duckdb.connect(":memory:")
        classes, last = [], None
        try:
            for s in pre:
                c.execute(s)
            n = len(d["stmts"])
            for i, s in enumerate(d["stmts"]):
                try:
                    cur = c.execute(s)
                    if i == n - 1:
                        rows = [oracle._norm(r) for r in cur.fetchall()]
                        last = ["ok", sorted(repr(r) for r in rows)]
                except Exception as e:             # noqa: BLE001
                    classes.append(type(e).__name__)
                    if i == n - 1:
                        last = ["error", type(e).__name__]
        finally:
            c.close()
        return {"classes": classes, "last": last}

    res = {"a": side(d["pre_a"])}
    if d.get("pre_b") is not None:
        res["b"] = side(d["pre_b"])
    print(json.dumps(res))


# ---------------------------------------------------------------- minimising
def ddmin(items: list, test) -> list:
    n = 2
    while len(items) >= 2:
        chunk = max(1, len(items) // n)
        reduced = False
        for i in range(0, len(items), chunk):
            cand = items[:i] + items[i + chunk:]
            if cand and test(cand):
                items, n, reduced = cand, max(n - 1, 2), True
                break
        if not reduced:
            if chunk == 1:
                break
            n = min(len(items), n * 2)
    return items


def min_optimizers(test, opts: list[str]) -> list[str]:
    cur = list(opts)
    for o in list(cur):
        t = [x for x in cur if x != o]
        if t and test(t):
            cur = t
    return cur


# ---------------------------------------------------------------- upstream search
def _gh(args: list[str], timeout: float = 60):
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
        return json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() else None
    except Exception:                              # noqa: BLE001
        return None


def _toks(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z_][a-z0-9_]{2,}", s.lower())}


def err_core(res: runner.Result | None) -> list[str]:
    if res is None:
        return []
    text = res.stderr + res.stdout
    m = re.search(r"INTERNAL Error: ([^\n]+)", text) or re.search(r"(Assertion[^\n]+)", text)
    if m:
        words = re.findall(r"[A-Za-z_]{4,}", re.sub(r'"[^"]*"', " ", m.group(1)))
        return words[:8]
    frames = re.findall(r"#\d+ 0x[0-9a-f]+ in (?:duckdb::)?([A-Za-z_:~]+)", text)
    return [f.split("::")[-1] for f in frames[:3]]


_CACHE = WORK / "results" / "upstream_cache.json"


def _cached(key: str, fn):
    try:
        c = json.loads(_CACHE.read_text()) if _CACHE.exists() else {}
    except Exception:                              # noqa: BLE001
        c = {}
    if key not in c:
        c[key] = fn()
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(c))
    return c[key]


CRASH_WORDS = ("sigsegv", "segmentation", "segfault", "null pointer", "nullptr", "heap-buffer",
               "use-after", "asan", "ubsan", "addresssanitizer", "stack-overflow", "abort")


def _score(hay: str, kws: list[str], opts: list[str], core: list[str], outcome: str) -> int:
    hay = hay.lower()
    pts = min(2, sum(k.lower() in hay for k in kws))
    pts += 2 * any(o.lower() in hay for o in opts)
    pts += "disable" in hay and bool(opts)
    pts += bool(core) and sum(w.lower() in hay for w in core) >= max(2, len(core) // 2)
    if outcome in ("asan", "crash"):
        pts += any(w in hay for w in CRASH_WORDS)
    elif outcome == "internal_error":
        pts += "internal error" in hay
    return int(pts)


def upstream_search(sql: list[str], opts: list[str], core: list[str], outcome: str = "") -> dict:
    """Find duplicate issues/PRs. strong = shares a feature keyword and scores >= 3."""
    text = " ".join(sql)
    kws = [k for k in FEATURES if re.search(rf"\b{k.replace(' ', r'\s+')}\b", text, re.I)][:3]
    crashw = ["crash", "segfault"] if outcome in ("asan", "crash") else ["INTERNAL Error"]
    queries: list[str] = []
    for k in kws[:2]:
        queries += [f"{k} {w}" for w in crashw]
        queries += [f"{k} {o}" for o in opts[:3]]
        queries.append(f"{k} disabled optimizer")
    if core:
        queries.append(" ".join(core[:5]))
    queries = list(dict.fromkeys(queries))[:10]
    bag = _toks(" ".join(queries + opts + kws))
    hits: dict[int, dict] = {}
    for q in queries:
        for kind in ("issues", "prs"):
            rows = _cached(f"{kind}|{q}", lambda: _gh(["search", kind, "--repo", REPO, q, "--limit", "8",
                           "--json", "number,title,state,createdAt,url"]) or []) or []
            for r in rows:
                r = dict(r, kind="pr" if kind == "prs" else "issue")
                r["tscore"] = len(_toks(r["title"]) & bag)
                if r["number"] not in hits or hits[r["number"]]["tscore"] < r["tscore"]:
                    hits[r["number"]] = r
    ranked = sorted(hits.values(), key=lambda r: -r["tscore"])[:8]
    for r in ranked[:6]:
        def fetch(r=r):
            if r["kind"] == "pr":
                d = _gh(["pr", "view", str(r["number"]), "--repo", REPO, "--json", "body"]) or {}
                try:
                    diff = subprocess.run(["gh", "pr", "diff", str(r["number"]), "--repo", REPO],
                                          capture_output=True, text=True, timeout=90).stdout
                except Exception:                  # noqa: BLE001
                    diff = ""
                return (d.get("body") or "") + "\n" + r["title"] + "\n" + diff[:60000]
            d = _gh(["issue", "view", str(r["number"]), "--repo", REPO, "--json", "body"]) or {}
            return r["title"] + "\n" + (d.get("body") or "")
        hay = _cached(f"text|{r['kind']}|{r['number']}", fetch) or ""
        r["score"] = _score(hay, kws, opts, core, outcome)
        r["kw_hit"] = any(k.lower() in hay.lower() for k in kws)
        r["strong"] = bool(r["kw_hit"] and r["score"] >= 3)
    ranked.sort(key=lambda r: (-int(r.get("strong", False)), -r.get("score", 0), -r["tscore"]))
    verdict = ("duplicate-likely" if any(r.get("strong") for r in ranked)
               else "similar-found" if any(r.get("score", 0) >= 2 for r in ranked) else "no-match")
    return {"verdict": verdict, "queries": queries, "matches": ranked}


# ---------------------------------------------------------------- triage entry points
def _check_on_main(sql: list[str], cfg_stmts: list[str], timeout: float = 30.0) -> dict:
    """N2: Run the minimised SQL on the pinned build-main/duckdb binary.

    Returns a dict with keys: outcome, reproduces_on_main, duration_s, available.
    If the binary does not exist the check is skipped (available=False).
    """
    if not MAIN_BIN.exists():
        return {"available": False, "outcome": None, "reproduces_on_main": None, "duration_s": None}
    import os as _os
    import time as _time
    env = dict(_os.environ)
    env.setdefault("ASAN_OPTIONS", "detect_leaks=0:abort_on_error=0")
    script = prelude(cfg_stmts) + "\n".join(sql) + "\n"
    t0 = _time.monotonic()
    try:
        p = subprocess.run(
            [str(MAIN_BIN), ":memory:", "-init", "/dev/null"],
            input=script, capture_output=True, text=True,
            timeout=timeout, env=env, errors="replace",
        )
        rc, stdout, stderr = p.returncode, p.stdout, p.stderr
        timed_out = False
    except subprocess.TimeoutExpired as e:
        rc = -1
        stdout = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = "timeout"
        timed_out = True
    dur = round(_time.monotonic() - t0, 2)
    # classify using the same logic as runner
    main_res = runner.classify(rc, stdout, stderr)
    reproduces = main_res.outcome in CRASHY and not timed_out
    return {
        "available": True,
        "binary": str(MAIN_BIN),
        "outcome": main_res.outcome,
        "reproduces_on_main": reproduces,
        "duration_s": dur,
        "timed_out": timed_out,
    }


def _finish(kind: str, sql: list[str], cfg_name: str, opts: list[str], res, stock: dict,
            origin: str, extra: dict) -> dict:
    core = err_core(res)
    sig_src = (res.signature if res is not None and res.signature else
               kind + "|" + "|".join(sql[-3:]) + "|" + ",".join(opts))
    sid = hashlib.sha1(sig_src.encode()).hexdigest()[:10]
    up = upstream_search(sql, opts, core, res.outcome if res is not None else "")
    # N2: check whether this finding reproduces on the pinned main build.
    cfgstmts = disable(opts) if opts else CFG.get(cfg_name, [])
    main_build = _check_on_main(sql, cfgstmts)
    f = {"id": sid, "kind": kind, "origin": origin, "config": cfg_name, "disabled_optimizers": opts,
         "minimal_sql": sql, "outcome": res.outcome if res is not None else "value_mismatch",
         "signature": res.signature if res is not None else "", "error_core": core,
         "stock_build": stock, "main_build": main_build,
         "upstream": up, "time": time.strftime("%Y-%m-%d %H:%M:%S"), **extra}
    d = OUT / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "repro.sql").write_text(prelude(cfgstmts) + "\n".join(sql) + "\n")
    (d / "meta.json").write_text(json.dumps(f, indent=1, default=str))
    if res is not None:
        (d / "stderr.txt").write_text(res.stderr[-6000:])
    return f


def _check_on_frozen_data(setup: list[str], query: str, cfg_stmts: list[str]) -> bool:
    """N8 gate: Determinism gate on frozen data.

    Executes setup once on a single in-memory database, then runs query under
    default and cfg_stmts on that exact same frozen data. If both produce identical
    normalized rows, the difference was purely non-deterministic setup taint.
    Returns True if identical (noise).
    """
    import duckdb
    c = duckdb.connect(":memory:")
    try:
        for s in setup:
            c.execute(s)
        # 1. Run under default config
        cur_a = c.cursor()
        rows_a = sorted(repr(oracle._norm(r)) for r in cur_a.execute(query).fetchall())

        # 2. Run under config B on the exact same database
        for s in cfg_stmts:
            c.execute(s)
        cur_b = c.cursor()
        rows_b = sorted(repr(oracle._norm(r)) for r in cur_b.execute(query).fetchall())
        return rows_a == rows_b
    except Exception:
        return False
    finally:
        c.close()


def triage_crash(stmts: list[str], origin: str = "") -> dict | None:
    # N8 gate: Quick mode for timeouts — avoid burning 5 configs x 60s
    if "timeout" in origin.lower():
        r_quick = cli(stmts, CFG["default"], timeout=3.0)
        if r_quick.outcome != "timeout":
            print(f"[-] {origin}: dropped (quick mode: timeout did not reproduce within 3s)", flush=True)
            return None

    for name in CRASH_ORDER:
        r = cli(stmts, CFG[name])
        if r.outcome in CRASHY:
            break
    else:
        return None
    sig, cfg_name = r.signature, name

    def same(sub, cfg):
        return cli(sub, cfg).signature == sig

    cfg = CFG[cfg_name]
    mn = ddmin(list(stmts), lambda sub: same(sub, cfg))
    opts: list[str] = []
    if cfg_name == "no_optimizers":
        opts = min_optimizers(lambda o: same(mn, disable(o)), list(oracle.OPTIMIZERS))
        cfg = disable(opts)
        mn = ddmin(mn, lambda sub: same(sub, cfg))
    final = cli(mn, cfg)
    stock = stock_replay(mn, cfg)
    stock["reproduces"] = bool(stock.get("segfault") or
                               "InternalException" in (stock.get("a") or {}).get("classes", []))
    return _finish("crash", mn, cfg_name, opts, final, stock, origin, {"n_statements": len(mn)})


def triage_value(setup: list[str], query: str, kind: str, differs: list[str],
                 origin: str = "") -> dict | None:
    # N8 gate: Unordered-window filter
    if sweep_oracle.is_unordered_window(query):
        print(f"[-] {origin}: dropped (unordered-window filter: window frame without ORDER BY)", flush=True)
        return None

    # N8 gate: By-design error rules (BITSTRING_AGG requires statistics_propagation)
    if re.search(r"\bbitstring_agg\b", query, re.I) and any("statistics_propagation" in d or "no_optimizers" in d for d in differs):
        print(f"[-] {origin}: dropped (by-design error rule: BITSTRING_AGG requires statistics_propagation)", flush=True)
        return None

    # N8 gate: Setup taint tracking
    if any(sweep_oracle.NONDET.search(s) for s in setup):
        print(f"[-] {origin}: dropped (setup taint tracking: setup contains non-deterministic functions)", flush=True)
        return None

    # N5: Metamorphic oracle triage (NoREC and TLP)
    if kind in ("norec", "tlp") or "/* vs */" in query:
        parts = [q.strip() for q in query.split("/* vs */", 1)]
        if len(parts) != 2:
            return None
        q1, q2 = parts

        def _norm_r(row):
            return [oracle._norm(x) for x in row]

        def bad_meta(sub_setup, cfg_stmts=None):
            import duckdb
            c = duckdb.connect(":memory:")
            try:
                for s in (cfg_stmts or []):
                    c.execute(s)
                for s in sub_setup:
                    c.execute(s)
                r1 = [_norm_r(r) for r in c.execute(q1).fetchall()]
                r2 = [_norm_r(r) for r in c.execute(q2).fetchall()]
                return r1 != r2
            except Exception:
                return False
            finally:
                c.close()

        # Verify reproduction under default configuration
        if not bad_meta(setup):
            return None

        mn_setup = ddmin(list(setup), bad_meta) if setup else []
        opts: list[str] = []
        cfg_name = "default"
        # Check if disabling optimizers eliminates the divergence
        if not bad_meta(mn_setup, CFG["no_optimizers"]):
            opts = min_optimizers(lambda o: bad_meta(mn_setup, disable(o)), list(oracle.OPTIMIZERS))

        final = mn_setup + [f"{q1};\n{q2};"]
        stock = {"reproduces": True, "kind": kind, "q1": q1, "q2": q2}
        return _finish(kind, final, cfg_name, opts, None, stock, origin, {
            "n_statements": len(final),
            "metamorphic_q1": q1,
            "metamorphic_q2": q2,
        })

    stmts = setup + [query]

    def bad(sub, cfg_b):
        r = stock_replay(sub, [], cfg_b)
        a, b = (r.get("a") or {}).get("last"), (r.get("b") or {}).get("last")
        if not a or not b:
            return False
        # N8 gate: by-design error rule — both configs rejected the query
        if a[0] == "error" and b[0] == "error":
            return False
        if kind == "value":
            return a[0] == "ok" and b[0] == "ok" and a[1] != b[1]
        return a[0] != b[0] or (a[0] == "error" and a[1] != b[1])

    cfg_name = next((n for n in differs if n in CFG and bad(stmts, CFG[n])), None)
    if cfg_name is None:
        return None
    cfg = CFG[cfg_name]

    # N8 gate: Determinism gate on frozen data
    if kind == "value" and _check_on_frozen_data(setup, query, cfg):
        print(f"[-] {origin}: dropped (determinism gate: identical on frozen data)", flush=True)
        return None

    mn_setup = ddmin(list(setup), lambda s: bad(s + [query], cfg)) if setup else []
    opts: list[str] = []
    if cfg_name == "no_optimizers":
        opts = min_optimizers(lambda o: bad(mn_setup + [query], disable(o)), list(oracle.OPTIMIZERS))
    final = mn_setup + [query]
    r = stock_replay(final, [], disable(opts) if opts else cfg)
    stock = {"reproduces": True, "a": r.get("a"), "b": r.get("b")}
    return _finish(kind, final, cfg_name, opts, None, stock, origin, {"n_statements": len(final)})


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "replay":
        _replay_child()
    elif len(sys.argv) >= 3 and sys.argv[1] == "crash":
        stmts = runner.split_statements(Path(sys.argv[2]).read_text(errors="replace"))
        f = triage_crash(stmts, sys.argv[2])
        print(json.dumps(f, indent=1, default=str) if f else "did not reproduce")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
