"""Generate mutants of seed scripts (reusing baseline.py's mutators) and write them to a corpus
directory that hunt.py can read, so the hunt runs queries nobody wrote.

    python harness/mutate_corpus.py --n 4000 --out $FUZZ_WORK/corpus/mutants/m1 \
        --new-vs /path/to/older/corpus/seeds --seed 1

--new-vs keeps only seeds that did not exist in the older corpus (i.e. what is new in this
DuckDB version). Then:   python harness/hunt.py --engine dev --mutants $FUZZ_WORK/corpus/mutants/m1
Each mutant is one .sql file; mutants.jsonl records which mutators produced it.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import baseline  # noqa: E402
import runner  # noqa: E402

WORK = runner.WORK
PLACEHOLDER = ("{TEMP_DIR}", "{DATA_DIR}", "__TEST_DIR__", "__WORKING_DIRECTORY__", "{WORKING_DIR}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", default=str(WORK / "corpus" / "seeds"))
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--new-vs", default="", help="older seed dir; keep only seeds that are not in it")
    ap.add_argument("--max-stmts", type=int, default=60, help="skip seeds longer than this")
    a = ap.parse_args()

    old = Path(a.new_vs) if a.new_vs else None
    seeds: list[list[str]] = []
    for f in sorted(Path(a.seeds).glob("*.sql")):
        if old and (old / f.name).exists():
            continue
        text = f.read_text(errors="replace")
        if any(p in text for p in PLACEHOLDER):
            continue
        stmts = runner.split_statements(text)
        if 0 < len(stmts) <= a.max_stmts:
            seeds.append(stmts)
    if not seeds:
        sys.exit("no usable seeds")
    rng = random.Random(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    counts: Counter = Counter()
    seen: set[str] = set()
    with open(out / "mutants.jsonl", "w") as log:
        made = tries = 0
        while made < a.n and tries < a.n * 5:
            tries += 1
            stmts, muts = baseline.mutate_script(seeds, rng)
            if muts == ["unmodified"]:
                continue
            body = "\n".join(stmts) + "\n"
            if body in seen:
                continue
            seen.add(body)
            name = f"{made:05d}__{muts[0]}.sql"
            (out / name).write_text(body)
            log.write(json.dumps({"file": name, "mutators": muts}) + "\n")
            counts.update(set(muts))
            made += 1
    print(f"{made} mutants from {len(seeds)} seeds -> {out}\nmutators used: {dict(counts)}")


if __name__ == "__main__":
    main()
