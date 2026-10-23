# Pointing the LLM at code that is new in DuckDB 2.0

Why: the one real bug so far (duckdb/duckdb#27759) is in 2.0-only code (the `delim_join_as_cte` rewrite, triggers), and
upstream's own issue stream does not cover the new triggers / planner code. An LLM trained before 2.0 also does not know
the new syntax, so unguided it burns prompts on old, heavily tested features or on statements that fail `EXPLAIN`.

## Build the target pack (data, not committed; ~1 minute)

```bash
export FUZZ_WORK=$HOME/cs5187            # your heavy-artifacts directory; DuckDB source checked out at tag v2.0.0
cd src
# 1. hints from the v2.0.0 source (the committed static/hints.json is from 1.5.6 and misses extension/ code)
python static/extract_hints.py --out $FUZZ_WORK/results/hints-v200.json --subsystems \
    src/optimizer src/planner src/execution src/function src/storage src/parser src/catalog src/main src/common \
    extension/core_functions extension/json extension/parquet
# 2. keep only hints inside lines added/changed since 1.5.6 (and drop generated / non-SQL-reachable files)
python static/new_code_hints.py --old v1.5.6 --new v2.0.0 \
    --hints $FUZZ_WORK/results/hints-v200.json --out $FUZZ_WORK/results/hints-v200-new.json
# 3. seeds from the tests ADDED since 1.5.6, then feature cards (valid 2.0 example scripts per feature)
#    (git diff --name-status v1.5.6 v2.0.0 -- test/sql | grep '^A' -> corpus/seeds-v200-added; see docs/RESEARCH_NOTES.md)
python llm/feature_cards.py build
```

## Run

```bash
python llm/generate.py --run new1 --targets hints \
    --hints $FUZZ_WORK/results/hints-v200-new.json --cards $FUZZ_WORK/results/feature-cards.json
python llm/loop.py --run newloop --hints $FUZZ_WORK/results/hints-v200-new.json \
    --cards $FUZZ_WORK/results/feature-cards.json --seed-dir $FUZZ_WORK/corpus/seeds-v200-added
```

Both options are off by default, so existing runs are unchanged. Keep a control arm (`--unguided`, or the old
`static/hints.json`) so the paper can compare "new-code targeted" against "unguided" on coverage of changed code and bugs.

## Notes
* A target = hint in changed code + the C++ snippet + 1-2 valid scripts for that feature (`--examples-per-target`).
* Validity is still checked with `EXPLAIN` and one repair round; model-written syntax for 2.0-only features is the main
  failure mode, which is why the examples come from DuckDB's own added tests.
* Anything the LLM finds must still be replayed alone and checked against upstream issues AND open PRs before it counts.
