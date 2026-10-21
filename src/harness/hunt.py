"""Bug hunt: run every script under DuckDB's own verification modes on the ASan build.

    python harness/hunt.py --workers 4 [--limit N] [--passes 1,2]

Inputs   corpus/seeds-v2 (or --seeds), test/fuzzer/** regression tests (past fuzzer
         bugs), corpus/llm/** (LLM-generated scripts)
Pass 1   every script x {verify, verify_parallel, verify_serializer, verify_external}
Pass 2   scripts that mention a feature x the optimizers that implement it
         (newer, less-tested optimizers), always with verification on
Keeps    crash-class results only: INTERNAL error, sanitizer report, crash and
         verification mismatches. (No value-diff oracle, so no data-nondeterminism noise.)
Then     de-duplicates by signature, minimises each unique hit (delta debugging) and
         searches upstream issues/PRs. Writes results/hunt/{hits.jsonl,HUNT_REPORT.md}.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import devcheck  # noqa: E402
import extract_seeds  # noqa: E402
import oracle  # noqa: E402
import runner  # noqa: E402
import triage  # noqa: E402

WORK = runner.WORK
OUT = WORK / "results" / "hunt"
ENGINE = "asan"          # "asan" = ASan CLI build, "dev" = newest published dev wheel
VERIFY = "PRAGMA enable_verification"
ALL_OPTS = oracle.OPTIMIZERS

PASS1_V1 = {                      # DuckDB 1.x: statement-level verification
    "verify": [VERIFY],
    "verify_parallel": [VERIFY, "PRAGMA verify_parallelism"],
    "verify_serializer": [VERIFY, "PRAGMA verify_serializer"],
    "verify_external": [VERIFY, "PRAGMA verify_external"],
}
VECMODE = "SET GLOBAL debug_verification_mode='%s'"
PASS1_V2 = {                      # DuckDB 2.x: enable_verification is a no-op; vector-level modes
    "default": [],
    "vec_vectors": [VECMODE % "VERIFY_VECTORS"],
    "vec_serialization": [VECMODE % "VERIFY_SERIALIZATION"],
    "vec_functions": [VECMODE % "VERIFY_FUNCTIONS"],
    "vec_compression": [VECMODE % "VERIFY_COMPRESSION"],
    "parallelism": ["PRAGMA verify_parallelism"],
    "roundtrip": [],              # print-and-reparse oracle, python engine only (see devcheck.run_roundtrip)
}
PASS1 = PASS1_V1
BASE_VERIFY = VERIFY              # the verification setting combined with optimizer switches in pass 2
ORACLE = "v1"
# feature keyword -> optimizers worth disabling for scripts that use it
PASS2_MAP = [
    (r"\bover\s*\(", ["window_self_join", "top_n_window_elimination", "window_rewriter", "partitioned_execution"]),
    (r"\bjoin\b", ["join_elimination", "join_order", "build_side_probe_side", "join_filter_pushdown",
                    "outer_join_simplification"]),
    (r"\bwith\b", ["cte_inlining", "materialized_cte", "common_subplan", "cte_filter_pusher"]),
    (r"\b(limit|order\s+by)\b", ["late_materialization", "top_n", "limit_pushdown"]),
    (r"\bunnest\b", ["unnest_rewriter"]),
    (r"\b(group\s+by|distinct|count|sum|avg|min|max)\b", ["duplicate_groups", "common_aggregate", "sum_rewriter",
                    "aggregate_function_rewriter", "aggregate_reuse", "distinct_aggregate_rewrite",
                    "partial_aggregate_pushdown"]),
    (r"\b(grouping\s+sets|rollup|cube)\b", ["grouping_sets"]),
    (r"\border\s+by\b", ["redundant_order_keys", "projection_pullup", "projection_placement"]),
    (r"\b(where|having)\b", ["filter_pushdown", "filter_pullup", "reorder_filter", "statistics_propagation",
                              "type_pushdown", "scalar_fn_pushdown"]),
]
VERIFY_RX = re.compile(r"statement differs from original result|verification failed|"
                       r"Unoptimized Result differs|result differs", re.I)
SKIP_VERIFY = re.compile(r"disable_verification|\brandom\s*\(|\buuid\s*\(|\bnow\s*\(|current_timestamp|"
                         r"gen_random_uuid|\bsetseed\b", re.I)
# Statements with side effects or approximate results legitimately differ when DuckDB re-runs
# them for verification ("Copied statement differs from original result!"), so they are
# removed from the scripts run under verification.
VOLATILE = re.compile(r"\b(nextval|currval|random|uuid|gen_random_uuid|now|today|current_(timestamp|time|date)|"
                      r"setseed|txid_current|approx_\w+|reservoir_quantile|sample|tablesample|"
                      r"pg_sleep|getenv|uuidv\d|shuffle|bitstring_agg)\b|using\s+sample", re.I)
CRASHY = {"internal_error", "asan", "crash", "verify_diff", "roundtrip"}
_tls = threading.local()


def discover(seeds: Path, llm: Path, fuzzer: Path, limit: int, old_seeds: Path | None = None,
             old_fuzzer: Path | None = None) -> list[tuple[str, str]]:
    """Most promising inputs first: LLM scripts, past-fuzzer regression tests, then seeds.
    old_seeds / old_fuzzer: skip inputs that already existed there (i.e. keep only what is NEW)."""
    items: list[tuple[str, str]] = []
    for f in sorted(llm.rglob("*.sql")):
        items.append((f"llm:{f.parent.name}/{f.name}", f.read_text(errors="replace")))
    for f in sorted(fuzzer.rglob("*.test")):
        if old_fuzzer and (old_fuzzer / f.relative_to(fuzzer)).exists():
            continue
        stmts = extract_seeds.parse(f.read_text(errors="replace"))
        if stmts:
            items.append((f"fuzzer:{f.parent.name}/{f.name}", "\n".join(stmts) + "\n"))
    for f in sorted(seeds.glob("*.sql")):
        if old_seeds and (old_seeds / f.name).exists():
            continue
        items.append((f"seed:{f.name}", f.read_text(errors="replace")))
    return items[:limit] if limit else items


def prelude(cfg: list[str]) -> str:
    return "".join(s.rstrip().rstrip(";") + ";\n" for s in cfg)


def _msg_sig(kind: str, msg: str) -> str:
    return f"{kind}:" + hashlib.sha1(re.sub(r"\d+", "N", msg[:240]).encode()).hexdigest()[:12]


def run_one(name: str, script: str, cfg_name: str, cfg: list[str], timeout: float = 60.0) -> dict:
    if not hasattr(_tls, "tmp"):
        _tls.tmp = tempfile.mkdtemp(prefix="hunt_tmp_")
    if cfg_name == "roundtrip":
        r = devcheck.run_roundtrip(script, VOLATILE.pattern, timeout)
        f0 = (r.get("finds") or [[""] * 4])[0]
        sig = _msg_sig("roundtrip", f0[0] + " " + re.sub(r"\s+", " ", f0[2])[:120]) if r["outcome"] == "roundtrip" else ""
        return {"script": name, "config": cfg_name, "outcome": r["outcome"], "sig": sig,
                "tail": ("%s | %s -> %s %s" % tuple(f0))[:700] if r["outcome"] == "roundtrip" else ""}
    if ENGINE == "dev":
        r = devcheck.run_script(script, [s.rstrip().rstrip(";") for s in cfg] +
                                [f"SET temp_directory='{_tls.tmp}'"], timeout)
        outcome = r["outcome"]
        err = next((m for c, m in r.get("errors", []) if c == "InternalException" or VERIFY_RX.search(m)), "")
        if outcome in ("internal_error", "verify_diff"):
            sig = _msg_sig(outcome, err)
        elif outcome == "crash":
            sig = "crash:" + hashlib.sha1((name + cfg_name).encode()).hexdigest()[:12]
        else:
            sig = ""
        return {"script": name, "config": cfg_name, "outcome": outcome, "sig": sig,
                "tail": err[-700:] if outcome in CRASHY else ""}
    sql = prelude(cfg + [f"SET temp_directory='{_tls.tmp}'"]) + script
    r = runner.run_sql(sql, "asan", timeout, cwd=WORK / "duckdb")
    text = r.stderr + "\n" + r.stdout
    outcome, sig = r.outcome, r.signature
    if not r.interesting and VERIFY_RX.search(text):
        m = VERIFY_RX.search(text)
        ctx = text[max(0, m.start() - 60): m.end() + 40]
        outcome = "verify_diff"
        sig = "verify_diff:" + hashlib.sha1(re.sub(r"\d+", "N", ctx).encode()).hexdigest()[:12]
    return {"script": name, "config": cfg_name, "outcome": outcome, "sig": sig,
            "tail": text[-700:] if outcome in CRASHY else ""}


def build_jobs(items, passes: set[int]):
    jobs = []
    for name, script in items:
        if ORACLE == "v1" and SKIP_VERIFY.search(script) and "disable_verification" in script.lower():
            continue                      # test explicitly turns verification off (v1 only)
        stmts = runner.split_statements(script)
        clean = [s for s in stmts if not VOLATILE.search(s)]
        if not clean:
            continue
        body = "\n".join(clean) + "\n"
        if 1 in passes:
            for cn, cfg in PASS1.items():
                if cn == "roundtrip" and ENGINE != "dev":
                    continue
                jobs.append((name, body, cn, cfg))
        if 2 in passes:
            low = body.lower()
            opts: list[str] = []
            for rx, os_ in PASS2_MAP:
                if re.search(rx, low):
                    opts += [o for o in os_ if o not in opts]
            for o in [x for x in opts if x in oracle.OPTIMIZERS][:8]:
                jobs.append((name, body, f"verify+no_{o}", [BASE_VERIFY] + triage.disable([o])))
    return jobs


def minimise(hit: dict, scripts: dict[str, str]) -> dict:
    stmts = runner.split_statements(scripts[hit["script"]])
    cfg_name = hit["config"]
    cfg = ([BASE_VERIFY] + triage.disable([cfg_name.split("no_", 1)[1]]) if cfg_name.startswith("verify+no_")
           else PASS1.get(cfg_name, [BASE_VERIFY]))
    sig = hit["sig"]

    def same(sub: list[str]) -> bool:
        return run_one("min", "\n".join(sub) + "\n", cfg_name, cfg)["sig"] == sig

    mn = triage.ddmin(stmts, same) if same(stmts) else stmts
    core = triage.err_core(runner.Result(hit["outcome"], 0, "", hit["tail"]))
    up = triage.upstream_search(mn, [cfg_name.split("no_", 1)[1]] if "no_" in cfg_name else [], core,
                                "internal_error" if hit["outcome"] == "verify_diff" else hit["outcome"])
    return {"minimal_sql": mn, "config_stmts": cfg, "error_core": core, "upstream": up,
            "reproduced_in_minimise": same(mn)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    seeds_default = WORK / "corpus" / "seeds-v2"
    if not seeds_default.exists():
        seeds_default = WORK / "corpus" / "seeds"
    ap.add_argument("--seeds", default=str(seeds_default))
    ap.add_argument("--llm", default=str(WORK / "corpus" / "llm"))
    ap.add_argument("--fuzzer", default=str(WORK / "duckdb" / "test" / "fuzzer"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--passes", default="1,2")
    ap.add_argument("--max-min", type=int, default=40, help="max unique hits to minimise")
    ap.add_argument("--engine", default="asan", choices=["asan", "dev"],
                    help="asan: ASan CLI build (default); dev: python wheel in $FUZZ_WORK/.venv-dev "
                         "(also the only engine with the print-and-reparse oracle)")
    ap.add_argument("--oracle", default="auto", choices=["auto", "v1", "v2"],
                    help="v1: enable_verification (DuckDB 1.x). v2: debug_verification_mode + round trip "
                         "(DuckDB 2.x; enable_verification is a no-op there). auto: from src/DUCKDB_VERSION")
    ap.add_argument("--force-dev", action="store_true", help="run even if the verification guard fails")
    ap.add_argument("--only", default="", help="comma list of pass-1 config names to run (default: all)")
    ap.add_argument("--exclude-seeds-dir", default="", help="skip seeds whose file already exists in this older seed dir")
    ap.add_argument("--exclude-fuzzer-dir", default="", help="skip regression tests that already exist in this older test/fuzzer dir")
    a = ap.parse_args()
    global ENGINE, OUT, ORACLE, PASS1, BASE_VERIFY
    ENGINE = a.engine
    ORACLE = a.oracle
    if ORACLE == "auto":
        ver = (HERE.parent / "DUCKDB_VERSION").read_text().strip() if (HERE.parent / "DUCKDB_VERSION").exists() else "v2"
        ORACLE = "v1" if ver.startswith("v1.") else "v2"
    if ORACLE == "v2":
        PASS1, BASE_VERIFY = PASS1_V2, VECMODE % "VERIFY_VECTORS"
    if a.only:
        keep = set(a.only.split(","))
        PASS1 = {k: v for k, v in PASS1.items() if k in keep}
    print(f"oracle: {ORACLE} | engine: {ENGINE} | work dir: {WORK} | pass-1 configs: {list(PASS1)}", flush=True)
    if ENGINE == "dev":
        OUT = WORK / "results" / "hunt-dev"
        if not devcheck.available():
            sys.exit("dev engine missing: run  python harness/devcheck.py --install")
        print(f"engine build: DuckDB {devcheck.version()}", flush=True)
    # ---- guards: never let an inert oracle produce a silent "0 hits" ----
    if ORACLE == "v1":
        if ENGINE == "dev":
            ctl = devcheck.run_script("CREATE SEQUENCE s;\nSELECT avg(nextval('s'));\n", ["PRAGMA enable_verification"])
            ok, why = ctl["outcome"] != "ok", "control query (nextval under enable_verification) did not fail"
        else:
            ok, why = True, ""
    else:
        if ENGINE == "dev":
            ok, detail = devcheck.setting_works("VERIFY_VECTORS")
            why = f"SET GLOBAL debug_verification_mode did not apply (got {detail!r})"
        else:
            r = runner.run_sql("SET GLOBAL debug_verification_mode='VERIFY_VECTORS';\n"
                               "SELECT current_setting('debug_verification_mode');\n", "asan", 60)
            ok, why = "VERIFY_VECTORS" in r.stdout.upper(), "debug_verification_mode did not apply on the CLI build"
    if not ok:
        msg = f"WARNING: oracle guard failed: {why}. Results would not mean what they claim."
        if not a.force_dev:
            sys.exit(msg + " Re-run with --force-dev to hunt for crashes / internal errors only.")
        print(msg, flush=True)
    else:
        print("oracle guard: ok", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    items = discover(Path(a.seeds), Path(a.llm), Path(a.fuzzer), a.limit,
                     Path(a.exclude_seeds_dir) if a.exclude_seeds_dir else None,
                     Path(a.exclude_fuzzer_dir) if a.exclude_fuzzer_dir else None)
    jobs = build_jobs(items, {int(x) for x in a.passes.split(",")})
    scripts = {j[0]: j[1] for j in jobs}          # the cleaned body that actually ran
    print(f"{len(items)} scripts, {len(jobs)} runs "
          f"({Counter(n.split(':')[0] for n, _ in items)})", flush=True)

    hits: list[dict] = []
    counts: Counter = Counter()
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex, open(OUT / "hits.jsonl", "w") as fh:
        for i, r in enumerate(ex.map(lambda j: run_one(j[0], j[1], j[2], j[3]), jobs), 1):
            counts[r["outcome"]] += 1
            if r["outcome"] in CRASHY:
                hits.append(r)
                fh.write(json.dumps(r) + "\n")
                fh.flush()
                print(f"[{i}] HIT {r['outcome']:12s} {r['config']:28s} {r['script']}  {r['sig']}", flush=True)
            if i % 1000 == 0:
                print(f"... {i}/{len(jobs)} {dict(counts)} {time.time()-t0:.0f}s", flush=True)
    print(f"runs done in {time.time()-t0:.0f}s: {dict(counts)}", flush=True)

    uniq: dict[str, list[dict]] = defaultdict(list)
    for h in hits:
        uniq[h["sig"]].append(h)
    print(f"{len(hits)} hits, {len(uniq)} unique signatures", flush=True)

    results = []
    for k, (sig, hs) in enumerate(uniq.items()):
        if k >= a.max_min:
            break
        h = hs[0]
        m = minimise(h, scripts)
        results.append({"sig": sig, "n_hits": len(hs), "example": h["script"], "config": h["config"],
                        "outcome": h["outcome"], "all_scripts": [x["script"] for x in hs][:8], **m})
        print(f"  [{k+1}/{min(len(uniq), a.max_min)}] {sig} {h['outcome']} -> {m['upstream']['verdict']} "
              f"({len(m['minimal_sql'])} stmts)", flush=True)
    order = {"no-match": 0, "similar-found": 1, "duplicate-likely": 2}
    results.sort(key=lambda r: (order.get(r["upstream"]["verdict"], 1), -r["n_hits"]))
    (OUT / "unique.json").write_text(json.dumps(results, indent=1, default=str))

    L = [f"# Hunt report - {time.strftime('%Y-%m-%d %H:%M')}", "",
         f"{len(items)} scripts, {len(jobs)} runs, {len(hits)} crash-class hits, {len(uniq)} unique.", "",
         "| signature | outcome | config | hits | minimal stmts | upstream |", "|---|---|---|---|---|---|"]
    for r in results:
        L.append(f"| {r['sig']} | {r['outcome']} | {r['config']} | {r['n_hits']} | {len(r['minimal_sql'])} | "
                 f"{r['upstream']['verdict']} |")
    for r in results:
        L += ["", f"## {r['sig']} ({r['upstream']['verdict']})", f"Example: `{r['example']}`, config `{r['config']}`", "",
              "```sql", triage.prelude(r["config_stmts"]) + "\n".join(r["minimal_sql"]), "```"]
        if r["error_core"]:
            L.append(f"Error: `{' '.join(r['error_core'])}`")
        for m in r["upstream"]["matches"][:4]:
            L.append(f"- {'**' if m.get('strong') else ''}#{m['number']} [{m['kind']}/{m['state']}] "
                     f"{m['title']}{'**' if m.get('strong') else ''} {m['url']}")
    (OUT / "HUNT_REPORT.md").write_text("\n".join(L) + "\n")
    print(f"report: {OUT / 'HUNT_REPORT.md'}", flush=True)


if __name__ == "__main__":
    main()
