"""Run every seed script once on a build and tabulate outcomes.

    python harness/sweep_seeds.py --build asan --workers 4 [--limit N]

Validates the extractor and classifier on realistic traffic, and surfaces any
seed that already trips an internal error / sanitizer report.
Writes results/sweep-<build>.jsonl (one row per seed) and prints a summary.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner  # noqa: E402

WORK = runner.WORK


def one(path: Path, build: str, timeout: float) -> dict:
    sql = path.read_text(errors="replace")
    t = time.time()
    r = runner.run_sql(sql, build, timeout, cwd=WORK / "duckdb")
    return {"seed": path.name, "outcome": r.outcome, "rc": r.returncode,
            "secs": round(time.time() - t, 2), "sig": r.signature,
            "errors": r.errors[:2], "stmts": len(runner.split_statements(sql)),
            "tail": r.stderr[-600:] if r.interesting else ""}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="asan", choices=list(runner.BUILDS))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    seeds = sorted((WORK / "corpus" / "seeds").glob("*.sql"))
    if a.limit:
        seeds = seeds[:a.limit]
    out = WORK / "results" / f"sweep-{a.build}.jsonl"
    out.parent.mkdir(exist_ok=True)
    counts: Counter = Counter()
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex, open(out, "w") as fh:
        for i, row in enumerate(ex.map(lambda p: one(p, a.build, a.timeout), seeds), 1):
            counts[row["outcome"]] += 1
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            if row["outcome"] in runner.INTERESTING:
                print(f"[{i}] {row['outcome']:15s} {row['seed']}  {row['sig']}", flush=True)
            if i % 100 == 0:
                print(f"... {i}/{len(seeds)}  {dict(counts)}  {time.time()-t0:.0f}s", flush=True)
    print(json.dumps({"seeds": len(seeds), "outcomes": counts, "secs": round(time.time() - t0)}))


if __name__ == "__main__":
    main()
