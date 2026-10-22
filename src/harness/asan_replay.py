"""Replay a JSONL query file on the ASan+UBSan build through the in-process batch driver.

    python harness/asan_replay.py --input results/funcfuzz-queries.jsonl --out results/asan-replay-func
    python harness/fuzz_functions.py --modes default,vec_vectors,vec_functions,vec_serialization,enable_verification \
        --emit-jsonl results/funcfuzz-queries.jsonl           # produces the input

Why: the Python wheel has no sanitizers, so signed overflow, out-of-bounds reads and use-after-free in a function
are silent there. Each line is {"id", "sql"} (a self-contained script); results are classified by runner.classify.
Findings (internal_error / asan / crash) are de-duplicated by signature, then replayed alone with
allocator_may_return_null=0 and no RSS limit: a hit that only appears under the campaign's resource caps is
labelled "resource artifact" and not counted.

Resumable: progress is kept in <out>/progress.json. Run under ASAN_OPTIONS with hard_rss_limit_mb set, never
under RLIMIT_AS (ASan reserves terabytes of address space).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402

INTERESTING = ("internal_error", "asan", "crash")
RESOURCE = re.compile(r"hard rss limit|rss limit exhausted|out-of-memory|requested allocation size|allocator is out of memory|"
                      r"allocation-size-too-big|exceeds maximum supported size|failed to allocate|cannot allocate memory|"
                      r"Out of Memory Error|\bKilled\b", re.I)
CONFIRM_ENV = {"ASAN_OPTIONS": "detect_leaks=0:abort_on_error=0:symbolize=1:allocator_may_return_null=0"}


def is_resource(r: "runner.Result") -> bool:
    """A cgroup/OOM kill (SIGKILL) or an allocation-failure report is a resource artifact, not a DuckDB bug."""
    return r.returncode in (-9, 137) or bool(RESOURCE.search(r.stderr + "\n" + r.stdout))


def confirm(sql: str) -> str:
    """Replay one finding alone, without the resource caps of the batch run (still inside the systemd scope)."""
    try:
        r = runner.run_sql(sql, build="asan", timeout=120, env_extra=CONFIRM_ENV)
    except Exception as e:                                   # noqa: BLE001
        return f"confirm failed: {e}"
    if r.outcome in INTERESTING:
        return "resource artifact (confirm)" if is_resource(r) else "reproduces alone"
    return f"NOT reproducible alone ({r.outcome})"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--chunk", type=int, default=200)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--limit", type=int, default=0, help="stop after N queries (0 = all)")
    ap.add_argument("--fresh", action="store_true", help="ignore progress.json")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    prog_path, hits_path = out / "progress.json", out / "hits.jsonl"
    done = 0
    counts: Counter = Counter()
    if prog_path.exists() and not a.fresh:
        st = json.loads(prog_path.read_text())
        done, counts = st["done"], Counter(st["counts"])
        print(f"resuming after {done} queries", flush=True)
    seen: set[str] = set()
    if hits_path.exists() and not a.fresh:
        seen = {json.loads(l)["signature"] for l in hits_path.open()}

    t0 = time.time()
    batch: list[tuple[str, str]] = []
    n = 0

    meta: dict[str, str] = {}

    def flush() -> None:
        nonlocal done
        if not batch:
            return
        res = runner.run_batch(batch, build="asan", timeout=a.timeout, isolate=True)
        for (qid, r), (_, sql) in zip(res, batch):
            counts[r.outcome] += 1
            if r.outcome not in INTERESTING:
                continue
            sig = r.signature or r.outcome
            if r.outcome in ("asan", "crash") and not runner._frames(r.stderr + r.stdout):
                sig = f"{r.outcome}:noframes:{meta.get(qid, qid)}"      # no stack in the report: do not collapse all crashes
            if sig in seen:
                counts["dup:" + r.outcome] += 1
                continue
            seen.add(sig)
            text = r.stderr + r.stdout
            verdict = "resource artifact" if is_resource(r) else confirm(sql)
            rec = {"id": qid, "outcome": r.outcome, "signature": sig, "verdict": verdict, "sql": sql[-1500:],
                   "evidence": " | ".join(r.errors[:2]) or text[:400]}
            with hits_path.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
            print(f"[{done + len(batch)}] {r.outcome:14s} {verdict:28s} {qid}  {rec['evidence'][:110]}", flush=True)
        done += len(batch)
        batch.clear()
        prog_path.write_text(json.dumps({"done": done, "counts": counts}))
        if (done // a.chunk) % 25 == 0:
            rate = (done - start) / max(time.time() - t0, 1e-9)
            print(f"... {done} queries, {rate:.0f} q/s, {dict(counts)}", flush=True)

    start = done
    with open(a.input) as fh:
        for line in fh:
            n += 1
            if n <= done:
                continue
            o = json.loads(line)
            meta[o["id"]] = o.get("fn") or o.get("title", "")[:40]
            batch.append((o["id"], o["sql"]))
            if len(batch) >= a.chunk:
                flush()
            if a.limit and done >= a.limit:
                break
    flush()
    print(f"finished: {done} queries in {time.time() - t0:.0f}s: {dict(counts)}", flush=True)


if __name__ == "__main__":
    main()
