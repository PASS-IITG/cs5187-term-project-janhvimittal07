"""Baseline (non-LLM) mutation fuzzer over the seed corpus (Task J6).

    python harness/baseline.py --build asan --minutes 30 --seed 1
    python harness/baseline.py --build cov  --minutes 30 --cov-every 25

Enhanced Mutations:
- boundary_sub: boundary literals (2047, 2048, 2049, HUGEINT bounds, NaN, inf, NULL, empty string)
- grammar_swap: grammar-aware swaps across joins, set ops, comparisons, window/aggregate functions
- nested_wrap: subquery wrapping, CTE wrapping, COALESCE/CASE/NULLIF wrapping
- stmt_splice: cross-seed statement splicing
- stmt_dup: statement duplication
- stmt_drop: statement deletion

Tracks and reports a per-mutator effectiveness table (attempts, ok, error, interesting, new_crashes).
Interesting outcomes (internal error, ASan, crash, timeout) are saved to crashes/<signature>.sql.
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

BOUNDARY = [
    "0", "1", "-1", "2", "255", "256", "1024", "2047", "2048", "2049", "4096", "65535", "65536",
    "2147483647", "-2147483648", "2147483648", "9223372036854775807",
    "-9223372036854775808", "170141183460469231731687303715884105727",
    "-170141183460469231731687303715884105728",
    "NULL", "0.0", "-0.0", "1e308", "-1e308", "'nan'::DOUBLE", "'inf'::DOUBLE", "'-inf'::DOUBLE",
    "''", "[]", "{}", "gen_random_uuid()",
]

GRAMMAR_SWAPS = [
    # Joins
    ("INNER JOIN", "LEFT JOIN"),
    ("LEFT JOIN", "FULL OUTER JOIN"),
    ("RIGHT JOIN", "LEFT JOIN"),
    ("FULL JOIN", "CROSS JOIN"),
    ("CROSS JOIN", "INNER JOIN"),
    ("LEFT JOIN", "ANTI JOIN"),
    ("LEFT JOIN", "SEMI JOIN"),
    # Set operations
    ("UNION ALL", "UNION"),
    ("UNION", "EXCEPT"),
    ("EXCEPT", "INTERSECT"),
    ("INTERSECT", "UNION ALL"),
    # Ordering & Window bounds
    ("ASC", "DESC"),
    ("DESC", "ASC"),
    ("NULLS FIRST", "NULLS LAST"),
    ("NULLS LAST", "NULLS FIRST"),
    ("ROWS BETWEEN", "RANGE BETWEEN"),
    ("RANGE BETWEEN", "GROUPS BETWEEN"),
    ("PRECEDING", "FOLLOWING"),
    ("FOLLOWING", "PRECEDING"),
    ("UNBOUNDED PRECEDING", "CURRENT ROW"),
    ("CURRENT ROW", "UNBOUNDED FOLLOWING"),
    # Aggregation & Analytic functions
    ("count(", "sum("),
    ("sum(", "avg("),
    ("min(", "max("),
    ("max(", "min("),
    ("avg(", "median("),
    ("arbitrary(", "first("),
    ("ROW_NUMBER()", "RANK()"),
    ("RANK()", "DENSE_RANK()"),
    # Comparisons & Predicates
    (" = ", " != "),
    (" != ", " = "),
    (" < ", " <= "),
    (" > ", " >= "),
    (" <= ", " > "),
    (" >= ", " < "),
    (" AND ", " OR "),
    (" OR ", " AND "),
    ("IS NULL", "IS NOT NULL"),
    ("IS NOT NULL", "IS NULL"),
    ("LIKE", "ILIKE"),
    ("NOT LIKE", "LIKE"),
    ("DISTINCT", ""),
    ("IN (", "NOT IN ("),
    ("TRUE", "FALSE"),
    ("FALSE", "TRUE"),
    # Type Casts
    ("::INTEGER", "::BIGINT"),
    ("::BIGINT", "::HUGEINT"),
    ("::VARCHAR", "::BLOB"),
    ("::DOUBLE", "::DECIMAL(18,4)"),
]

NUM = re.compile(r"(?<![\w.'\"])\d+(?![\w.'\"])")


class MutatorTracker:
    """Tracks applied mutation operators and their outcome statistics."""

    def __init__(self) -> None:
        self.stats: dict[str, dict[str, int]] = {}

    def record(self, mutators: list[str], outcome: str, is_new_crash: bool = False) -> None:
        for m in set(mutators):
            st = self.stats.setdefault(
                m, {"attempts": 0, "ok": 0, "error": 0, "interesting": 0, "new_crashes": 0}
            )
            st["attempts"] += 1
            if outcome == "ok":
                st["ok"] += 1
            elif outcome in ("crash", "asan", "internal_error", "timeout"):
                st["interesting"] += 1
            else:
                st["error"] += 1
            if is_new_crash:
                st["new_crashes"] += 1

    def to_dict(self) -> dict[str, dict[str, int]]:
        return self.stats

    def format_table(self) -> str:
        lines = [
            "\n=== Per-Mutator Effectiveness Table ===",
            f"{'Mutator':<16s} {'Attempts':>9s} {'Ok':>7s} {'Error':>7s} {'Inter':>7s} {'Crash':>7s} {'Valid %':>8s}",
            "-" * 68,
        ]
        sorted_muts = sorted(self.stats.items(), key=lambda kv: -kv[1]["attempts"])
        for name, st in sorted_muts:
            att = st["attempts"]
            ok = st["ok"]
            err = st["error"]
            itr = st["interesting"]
            cra = st["new_crashes"]
            valid_pct = round(100.0 * (ok + itr) / att, 1) if att else 0.0
            lines.append(
                f"{name:<16s} {att:9,d} {ok:7,d} {err:7,d} {itr:7,d} {cra:7,d} {valid_pct:7.1f}%"
            )
        return "\n".join(lines)


def load_seeds() -> list[list[str]]:
    out = []
    for f in sorted(SEEDS.glob("*.sql")):
        stmts = runner.split_statements(f.read_text(errors="replace"))
        if stmts:
            out.append(stmts)
    return out


def mutate_boundary(s: str, rng: random.Random) -> tuple[str, bool]:
    """Substitute numeric literals with boundary/extreme values."""
    nums = list(NUM.finditer(s))
    if nums:
        m = rng.choice(nums)
        sub = rng.choice(BOUNDARY)
        return s[:m.start()] + sub + s[m.end():], True
    return s, False


def mutate_grammar_swap(s: str, rng: random.Random) -> tuple[str, bool]:
    """Grammar-aware keyword and operator swaps."""
    cands = [(a, b) for a, b in GRAMMAR_SWAPS if a.lower() in s.lower()]
    if cands:
        a, b = rng.choice(cands)
        return re.sub(re.escape(a), b, s, count=1, flags=re.I), True
    return s, False


def mutate_nested_wrap(s: str, rng: random.Random) -> tuple[str, bool]:
    """Wrap queries and expressions into nested subqueries, CTEs, or CASE/COALESCE."""
    s_strip = s.strip()
    s_clean = s_strip.rstrip(";")

    # 1. Query wrapping for SELECT statements
    if s_clean.lower().startswith("select"):
        wrap_type = rng.random()
        if wrap_type < 0.35:
            # Subquery wrapping
            alias = f"subq_{rng.randint(10, 99)}"
            return f"SELECT * FROM ({s_clean}) AS {alias};", True
        elif wrap_type < 0.70:
            # CTE (WITH) wrapping
            cte_name = f"cte_{rng.randint(10, 99)}"
            return f"WITH {cte_name} AS ({s_clean}) SELECT * FROM {cte_name};", True
        else:
            # Aggregate / limit wrapping
            alias = f"_agg_{rng.randint(10, 99)}"
            limit_val = rng.choice(["2047", "2048", "2049", "10"])
            return f"SELECT count(*), min(1) FROM ({s_clean}) AS {alias} LIMIT {limit_val};", True

    # 2. Expression wrapping for WHERE clauses
    if " where " in s_clean.lower():
        parts = re.split(r"\s+where\s+", s_clean, maxsplit=1, flags=re.I)
        if len(parts) == 2:
            clause = parts[1]
            wrapped_clause = rng.choice([
                f"COALESCE(({clause}), false)",
                f"CASE WHEN ({clause}) THEN true ELSE false END",
                f"NOT (NOT ({clause}))",
            ])
            return f"{parts[0]} WHERE {wrapped_clause};", True

    return s, False


def mutate_stmt(s: str, rng: random.Random) -> tuple[str, list[str]]:
    """Apply one or more statement-level mutations."""
    applied: list[str] = []
    r = rng.random()

    if r < 0.40:
        s, ok = mutate_boundary(s, rng)
        if ok:
            applied.append("boundary_sub")
    elif r < 0.75:
        s, ok = mutate_grammar_swap(s, rng)
        if ok:
            applied.append("grammar_swap")
    else:
        s, ok = mutate_nested_wrap(s, rng)
        if ok:
            applied.append("nested_wrap")

    # If first choice was not applicable, try boundary fallback
    if not applied:
        s, ok = mutate_boundary(s, rng)
        if ok:
            applied.append("boundary_sub")

    return s, applied


def mutate_script(seeds: list[list[str]], rng: random.Random) -> tuple[list[str], list[str]]:
    """Mutate an entire seed script, tracking all applied mutation operators."""
    base = list(rng.choice(seeds))
    applied_mutators: list[str] = []

    for _ in range(rng.randint(1, 4)):
        op = rng.random()
        i = rng.randrange(len(base))

        if op < 0.55:
            # Mutate existing statement
            base[i], muts = mutate_stmt(base[i], rng)
            applied_mutators.extend(muts)
        elif op < 0.75 and len(base) > 2:
            # Drop statement
            del base[i]
            applied_mutators.append("stmt_drop")
        elif op < 0.90:
            # Cross-seed statement splicing
            other_seed = rng.choice(seeds)
            if other_seed:
                other_stmt = rng.choice(other_seed)
                base.insert(rng.randrange(len(base) + 1), other_stmt)
                applied_mutators.append("stmt_splice")
        else:
            # Duplicate statement
            base.insert(i, base[i])
            applied_mutators.append("stmt_dup")

    return base, applied_mutators or ["unmodified"]


def save_crash(res: runner.Result, stmts: list[str]) -> bool:
    CRASHES.mkdir(exist_ok=True)
    p = CRASHES / (res.signature.replace(":", "_") + ".sql")
    if p.exists():
        return False
    p.write_text("\n".join(stmts) + "\n")
    p.with_suffix(".log").write_text(f"{res.outcome} rc={res.returncode}\n{res.stderr[-4000:]}\n")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
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
    tracker = MutatorTracker()
    counts: dict[str, int] = {}
    new_crashes = 0
    t0 = time.time()
    n = 0

    with open(a.log, "a") as log:
        while time.time() - t0 < a.minutes * 60:
            stmts, muts = mutate_script(seeds, rng)
            sql = "\n".join(stmts) + "\n"
            res = cov.run_and_merge(sql, a.timeout) if cov else runner.run_sql(sql, a.build, a.timeout, cwd=WORK / "duckdb")
            n += 1
            counts[res.outcome] = counts.get(res.outcome, 0) + 1

            is_new = False
            if res.interesting and save_crash(res, stmts):
                new_crashes += 1
                is_new = True
                print(f"[{n}] NEW {res.signature}", file=sys.stderr)

            tracker.record(muts, res.outcome, is_new)

            row = {
                "t": round(time.time() - t0, 1),
                "n": n,
                "outcome": res.outcome,
                "mutators": muts,
            }
            if cov and a.cov_every and n % a.cov_every == 0:
                row["cov"] = cov.summary()
                print(f"[{n}] {row['cov']}", file=sys.stderr)
            log.write(json.dumps(row) + "\n")
            log.flush()

    # Print summary and per-mutator effectiveness table
    print(tracker.format_table(), file=sys.stderr)
    print(json.dumps({
        "runs": n,
        "outcomes": counts,
        "new_crashes": new_crashes,
        "mutators": tracker.to_dict(),
    }))


if __name__ == "__main__":
    main()
