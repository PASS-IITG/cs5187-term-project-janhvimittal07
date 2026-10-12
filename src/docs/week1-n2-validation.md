# Week 1 — N2: Pinned-main validation

- Required DuckDB revision: `690d113c24dad3b37529c1518354bcbe00d8ffbc`
- Build script: `build-main.sh`
- Build verification: passed; `SELECT 1 + 1` returned `2`.
- ASan binary: `~/fuzzwork/build-asan/duckdb`
- Pinned-main binary: `~/fuzzwork/build-main/duckdb`
- Saved finding tested: `results/triage/0c5a16d788/repro.sql`
- Checker finding ID: `6df259442da8`
- ASan outcome: `binder_error`
- Pinned-main outcome: `binder_error`
- Checker result: `reproduces_on_main = false`
- Evidence: `~/fuzzwork/results/triage/6df259442da8/`

Interpretation: this SQL did not reproduce as the same crash on the pinned build.
Both runs returned a binder error, so this comparison does not establish a confirmed bug
or prove that a bug is fixed. The finding needs review before being treated as a defect.
