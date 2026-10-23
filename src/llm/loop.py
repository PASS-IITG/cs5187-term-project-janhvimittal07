"""Coverage-guided generation loop (Task P3).

    python llm/loop.py --run L1 --rounds 5 --prompts 4 --per-prompt 8 --warm-seeds 40
    python llm/loop.py --run L1u --rounds 5 --prompts 4 --unguided      # control arm

Each round:
  1. take the biggest/hint-richest code regions that no query has executed yet
  2. ask the LLM for queries that should reach each region (generate.py)
  3. run every resulting script on the coverage build (cumulative profile)
     and on the ASan build (crash / sanitizer reports are kept in crashes/)
  4. recompute coverage; record whether each targeted region was actually hit
One JSON row per round goes to results/loop-<run>.jsonl (data for RQ1/RQ2).
--unguided asks for the same number of queries without any code target, which is
the control that shows whether the coverage feedback adds anything.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC / "harness"))
sys.path.insert(0, str(SRC / "coverage"))
import runner  # noqa: E402
from client import LLM, LLMError  # noqa: E402
from cov import Coverage  # noqa: E402
from generate import _fmt_target, configure, generate_batch, load_schemas  # noqa: E402

WORK = runner.WORK
FILTERS = ["src/optimizer", "src/planner", "src/execution", "src/function", "src/storage"]


def region_hit(cov: Coverage, file: str, start: int, end: int) -> bool:
    ent = cov.lcov().get(file)
    if not ent:
        return False
    return any(c > 0 for n, c in ent[0].items() if start <= n <= end)


def run_script(cov: Coverage, sql: str, timeout: float, asan: bool) -> str:
    """Run on the coverage build (merging profile) and optionally on ASan; return outcome."""
    r = cov.run_and_merge(sql, timeout)
    outcome = r.outcome
    if asan and runner.BUILDS["asan"].exists():
        a = runner.run_sql(sql, "asan", timeout, cwd=WORK / "duckdb")
        if a.interesting:
            d = WORK / "crashes"
            d.mkdir(exist_ok=True)
            p = d / ("llm_" + a.signature.replace(":", "_") + ".sql")
            if not p.exists():
                p.write_text(sql)
                p.with_suffix(".log").write_text(f"{a.outcome} rc={a.returncode}\n{a.stderr[-4000:]}\n")
            outcome = a.outcome
    return outcome


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--prompts", type=int, default=4, help="prompts per round")
    ap.add_argument("--per-prompt", type=int, default=8)
    ap.add_argument("--warm-seeds", type=int, default=40, help="seed scripts run first for a starting profile")
    ap.add_argument("--schemas", default=str(WORK / "gen_out"))
    ap.add_argument("--unguided", action="store_true")
    ap.add_argument("--no-asan", action="store_true")
    ap.add_argument("--rank", default="hints", choices=["hints", "sinks", "size"])
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--hints", default="", help="hints JSON used to rank uncovered regions (e.g. results/hints-v200-new.json)")
    ap.add_argument("--cards", default="", help="feature-cards JSON: valid 2.0 example scripts appended to each target")
    ap.add_argument("--examples-per-target", type=int, default=2)
    ap.add_argument("--seed-dir", default="", help="warm-start seed directory (default corpus/seeds; use corpus/seeds-v200-added)")
    a = ap.parse_args()

    rng = random.Random(a.seed)
    schemas = load_schemas(Path(a.schemas))
    if not schemas:
        sys.exit(f"no schemas in {a.schemas}; run: python gen/generate_schema.py --out-dir {a.schemas}")
    names = sorted(schemas)
    llm = LLM()
    configure(a.cards, "", a.examples_per_target)
    if a.hints:
        import cov as covmod
        covmod.DEFAULT_HINTS = Path(a.hints)
    cov = Coverage(WORK / "results" / f"cov-loop-{a.run}")
    out_dir = WORK / "corpus" / "llm" / a.run
    out_dir.mkdir(parents=True, exist_ok=True)
    log = WORK / "results" / f"loop-{a.run}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)

    # warm start: run some seeds so there is a coverage profile to diff against
    seeds = sorted((Path(a.seed_dir) if a.seed_dir else WORK / "corpus" / "seeds").glob("*.sql"))
    rng.shuffle(seeds)
    for s in seeds[:a.warm_seeds]:
        cov.run_and_merge(s.read_text(errors="replace"), a.timeout)
    base = cov.summary(FILTERS)
    print(f"warm start: {base}", flush=True)

    targeted: set[tuple[str, int]] = set()
    t0 = time.time()
    for rnd in range(1, a.rounds + 1):
        before = cov.summary(FILTERS)
        if a.unguided:
            targets = [{"text": "", "file": "", "start": 0, "end": 0}] * a.prompts
        else:
            regs = [r for r in cov.uncovered_regions(FILTERS, top=200, by=a.rank, context=3)
                    if r.snippet and (r.file, r.start) not in targeted]
            targets = []
            for r in regs[:a.prompts]:
                targeted.add((r.file, r.start))
                targets.append({"text": _fmt_target(r.file, r.start, r.end, r.snippet),
                                "file": r.file, "start": r.start, "end": r.end})
        gen = valid = repaired = 0
        outcomes: Counter = Counter()
        scripts: list[tuple[dict, str]] = []
        for k, tg in enumerate(targets):
            name = rng.choice(names)
            try:
                r = generate_batch(llm, schemas[name], tg, a.per_prompt, a.temperature)
            except LLMError as e:
                print(f"[r{rnd}.{k}] LLM error: {e}", file=sys.stderr)
                continue
            gen, valid, repaired = gen + r["n_generated"], valid + r["n_valid"], repaired + r["n_repaired"]
            if not r["queries"]:
                continue
            sql = schemas[name].rstrip() + "\n\n" + "\n".join(r["queries"]) + "\n"
            (out_dir / f"r{rnd:02d}_p{k}__{name}.sql").write_text(sql)
            scripts.append((tg, sql))
        for _, sql in scripts:
            outcomes[run_script(cov, sql, a.timeout, not a.no_asan)] += 1
        after = cov.summary(FILTERS)
        hit = [region_hit(cov, t["file"], t["start"], t["end"]) for t, _ in scripts if t["file"]]
        row = {"round": rnd, "t_sec": round(time.time() - t0, 1), "guided": not a.unguided,
               "n_generated": gen, "n_valid": valid, "n_repaired": repaired, "n_scripts": len(scripts),
               "outcomes": dict(outcomes), "lines_hit": after["lines_hit"], "lines_total": after["lines"],
               "branches_hit": after["branches_hit"], "new_lines": after["lines_hit"] - before["lines_hit"],
               "new_branches": after["branches_hit"] - before["branches_hit"],
               "targets": len(hit), "targets_reached": sum(hit)}
        with open(log, "a") as fh:
            fh.write(json.dumps(row) + "\n")
        print(f"[round {rnd}] gen={gen} valid={valid} scripts={len(scripts)} "
              f"+{row['new_lines']} lines +{row['new_branches']} branches "
              f"targets reached {row['targets_reached']}/{row['targets']} "
              f"lines {after['lines_hit']}/{after['lines']} {dict(outcomes)}", flush=True)
    print(f"done in {time.time()-t0:.0f}s: coverage {base['lines_hit']} -> {cov.summary(FILTERS)['lines_hit']} lines")


if __name__ == "__main__":
    main()
