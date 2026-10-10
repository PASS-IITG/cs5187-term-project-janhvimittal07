"""Baseline (non-LLM) mutation fuzzer over the seed corpus.

    python harness/baseline.py --build asan --minutes 30 --seed 1
    python harness/baseline.py --build cov  --minutes 30 --cov-every 25

Mutations: boundary-literal substitution, keyword swaps, statement splicing
between seeds, statement duplication / dropping. Interesting outcomes
(internal error, ASan, crash, timeout) are saved to crashes/<signature>.sql.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("FUZZ_WORK", Path(__file__).resolve().parent.parent))  # duckdb/, build-*/, corpus/, results/
sys.path.insert(0, str(CODE / "harness"))
sys.path.insert(0, str(CODE / "coverage"))
import runner  # noqa: E402

SEEDS = WORK / "corpus" / "seeds"
CRASHES = WORK / "crashes"

BOUNDARY = ["0", "1", "-1", "2", "255", "256", "2047", "2048", "2049", "4096", "65535", "65536",
            "2147483647", "-2147483648", "2147483648", "9223372036854775807",
            "-9223372036854775808", "170141183460469231731687303715884105727",
            "NULL", "0.0", "1e308", "'nan'::DOUBLE", "'inf'::DOUBLE", "''", "[]", "{}"]
SWAPS = [("INNER JOIN", "LEFT JOIN"), ("LEFT JOIN", "FULL JOIN"), ("UNION ALL", "UNION"),
         ("ASC", "DESC"), ("DISTINCT", ""), ("ROWS BETWEEN", "RANGE BETWEEN"),
         ("PRECEDING", "FOLLOWING"), ("count(", "sum("), ("min(", "max("),
         ("NULLS FIRST", "NULLS LAST"), ("EXCEPT", "INTERSECT")]
NUM = re.compile(r"(?<![\w.'\"])\d+(?![\w.'\"])")


def load_seeds() -> list[list[str]]:
    out = []
    for f in sorted(SEEDS.glob("*.sql")):
        stmts = runner.split_statements(f.read_text(errors="replace"))
        if stmts:
            out.append(stmts)
    return out


def mutate_stmt(s: str, rng: random.Random) -> str:
    r = rng.random()
    if r < 0.5:
        nums = list(NUM.finditer(s))
        if nums:
            m = rng.choice(nums)
            return s[:m.start()] + rng.choice(BOUNDARY) + s[m.end():]
    if r < 0.8:
        cands = [(a, b) for a, b in SWAPS if a.lower() in s.lower()]
        if cands:
            a, b = rng.choice(cands)
            return re.sub(re.escape(a), b, s, count=1, flags=re.I)
    return s


def mutate_script(seeds: list[list[str]], rng: random.Random) -> list[str]:
    base = list(rng.choice(seeds))
    for _ in range(rng.randint(1, 4)):
        op = rng.random()
        i = rng.randrange(len(base))
        if op < 0.55:
            base[i] = mutate_stmt(base[i], rng)
        elif op < 0.75 and len(base) > 2:
            del base[i]
        elif op < 0.9:
            other = rng.choice(rng.choice(seeds))
            base.insert(rng.randrange(len(base) + 1), other)
        else:
            base.insert(i, base[i])
    return base


def save_crash(res: runner.Result, stmts: list[str]) -> bool:
    CRASHES.mkdir(exist_ok=True)
    p = CRASHES / (res.signature.replace(":", "_") + ".sql")
    if p.exists():
        return False
    p.write_text("\n".join(stmts) + "\n")
    p.with_suffix(".log").write_text(f"{res.outcome} rc={res.returncode}\n{res.stderr[-4000:]}\n")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="asan", choices=list(runner.BUILDS))
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--cov-every", type=int, default=0, help="log coverage summary every N runs (cov build only)")
    ap.add_argument("--log", default=str(WORK / "results" / "baseline.jsonl"))
    a = ap.parse_args()

    rng = random.Random(a.seed)
    seeds = load_seeds()
    print(f"{len(seeds)} seed scripts", file=sys.stderr)
    cov = None
    if a.build == "cov":
        from cov import Coverage
        cov = Coverage(WORK / "results" / f"cov-baseline-{a.seed}")
    Path(a.log).parent.mkdir(exist_ok=True)
    counts: dict[str, int] = {}
    new_crashes = 0
    t0 = time.time()
    n = 0
    with open(a.log, "a") as log:
        while time.time() - t0 < a.minutes * 60:
            stmts = mutate_script(seeds, rng)
            sql = "\n".join(stmts) + "\n"
            res = cov.run_and_merge(sql, a.timeout) if cov else runner.run_sql(sql, a.build, a.timeout, cwd=WORK / "duckdb")
            n += 1
            counts[res.outcome] = counts.get(res.outcome, 0) + 1
            if res.interesting and save_crash(res, stmts):
                new_crashes += 1
                print(f"[{n}] NEW {res.signature}", file=sys.stderr)
            row = {"t": round(time.time() - t0, 1), "n": n, "outcome": res.outcome}
            if cov and a.cov_every and n % a.cov_every == 0:
                row["cov"] = cov.summary()
                print(f"[{n}] {row['cov']}", file=sys.stderr)
            log.write(json.dumps(row) + "\n")
            log.flush()
    print(json.dumps({"runs": n, "outcomes": counts, "new_crashes": new_crashes}))


if __name__ == "__main__":
    main()
