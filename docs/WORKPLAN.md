# Work plan — Directed LLM-guided fuzzing of DuckDB

Status as of 2026-10-10 (rebalanced so the three workloads are about equal). Read `src/README.md` first for how to build and run.

## 1. Where we are

A working pipeline exists end to end **except the LLM generator and the static
hints**. `python harness/fuzz.py all` runs: seed extraction, an ASan crash sweep,
a differential sweep under 5 configs, optional baseline mutation fuzzing, and
automatic triage (reproduce, minimise, confirm on the stock build, search
upstream issues/PRs) into `results/REPORT.md`.

Measured so far (DuckDB v1.5.6, 1,959 seed scripts from DuckDB's own tests):

| Experiment | Result |
|---|---|
| ASan crash sweep | 962 ok / 531 binder / 336 runtime / 129 parse / 1 timeout / **0 crashes** |
| Differential sweep (5 configs, 11k queries) | 2 real crashes, **both already known upstream** |

The two crashes (both need `SET disabled_optimizers=...`):

| Bug | Upstream |
|---|---|
| SIGSEGV: ASOF join with a non-comparison condition (`join_order` disabled) | issue #26300, fixed by PR #26301 (merged 2026-10-08) |
| INTERNAL Error "Failed to bind column reference rowid": `MERGE ... WHEN NOT MATCHED BY SOURCE` (`build_side_probe_side` disabled) | fix PR #26715 (open) |

**Takeaway:** the method finds real bugs, but upstream is already running a
disabled-optimizer CI (#26292), so that vein closes quickly. We need a bug
nobody has reported. The LLM-guided part is what is supposed to get us there.

## 2. Rules for everyone

- Before calling anything a finding, search **issues and open/merged PRs** in
  `duckdb/duckdb`, and test the latest `main` (a fix may already be merged).
  `triage.py` does a first pass; a human confirms.
- Never report upstream without agreeing in the group first.
- Every number in the paper must come from a run of code in `src/`, with the
  raw output copied to `paper/data/` (see the rules in the root `README.md`).
- Commit your own work under your own name. Small PRs or direct commits to
  `main` are both fine; do not rewrite shared history.
- Declare AI-assisted code in the paper's disclosure.

## 3. How the work is split

Each person gets **one core component (hard), three supporting tasks, one share
of evaluation and one share of the paper**. Effort points: S = 1, M = 2, L = 3
(about a day, a few days, about a week). Everyone ends up with ~14 points.

| | Niket | Jahnvi | Prabhnoor |
|---|---|---|---|
| **Theme** | Execution engine, oracles, baselines, campaigns | Program analysis, coverage gaps, test data | LLM generation and the feedback loop |
| **Core (L)** | N3 in-process batch driver | J2 hint extraction | P2 generator with validity repair |
| **Evaluation** | RQ4 bugs found + OSS-Fuzz/SQLsmith baselines | RQ3 effect of static hints | RQ1/RQ2 coverage and valid-query rate |
| **Points** | 14 | 14 | 14 |

### Who owns which files (review, understand, improve, and be able to explain)

| Owner | Files |
|---|---|
| Niket | `src/build.sh`, `src/setup.sh`, `src/harness/runner.py`, `src/harness/smoke_test.py`, `src/harness/oracle.py`, `src/harness/sweep_oracle.py`, `src/harness/sweep_seeds.py`, `src/harness/triage.py`, `src/harness/campaign.py`, `src/driver/`, `src/baselines/` |
| Jahnvi | `src/static/`, `src/gen/`, `src/coverage/cov.py`, `src/harness/extract_seeds.py`, `src/harness/baseline.py` |
| Prabhnoor | `src/llm/`, `src/eval/`, `src/harness/fuzz.py` |

### Where each task lives (skeleton files already exist; replace the stubs)

| Task | File(s) |
|---|---|
| N1, N7 | `src/build.sh`, `src/setup.sh` (+ new `Dockerfile`) |
| N2 | `src/build.sh` (add `main` build), `src/harness/triage.py` |
| N3 | `src/driver/batch_driver.cpp`, `src/driver/README.md` |
| N4 | `src/baselines/run_ossfuzz.sh`, `src/baselines/run_sqlsmith.sh` |
| N5 | `src/harness/oracle.py`, `src/harness/sweep_oracle.py` |
| N6 | `src/harness/campaign.py` |
| J1, J2 | `src/static/extract_hints.py`, `src/static/queries/`, `src/static/hints.schema.json` |
| J3 | `src/coverage/cov.py` |
| J4 | `src/gen/gen_schemas.py` |
| J5 | `src/harness/extract_seeds.py` |
| J6 | `src/harness/baseline.py` |
| J7 | `src/eval/` (add `hints_eval.py`), results to `paper/data/` |
| P1 | `src/llm/client.py` |
| P2, P3 | `src/llm/generate.py`, `src/harness/fuzz.py` (`generate` stage) |
| P4 | `src/llm/prompts/` |
| P5, P7 | `src/eval/run_eval.py`, `src/eval/README.md` |
| P6 | `paper/HANDOFF.md`, `paper/sections/*.tex` |

Nobody edits another person's file without telling them; ask in the group chat
or open a small PR.

## 4. Niket — execution engine, oracles, baselines, campaigns

| # | Task | Pts | Why |
|---|---|---|---|
| N1 | Review ASan/UBSan and coverage flags in `build.sh` (assertions, frame pointers, `ASAN_OPTIONS`/`UBSAN_OPTIONS`, extension set); document build time and RAM | S | Everything runs on these binaries |
| N2 | Add a build of DuckDB `main` at a pinned commit; add a "reproduces on main?" step to `triage.py` | M | Two of our "bugs" were already fixed on main |
| N3 | **Core:** in-process batch driver (C API or C++) that runs many scripts per process, with crash isolation and a per-query timeout | L | Process start-up dominates run time; needed for long campaigns |
| N4 | Baselines under the same time budget: **OSS-Fuzz targets** (see google/oss-fuzz `projects/duckdb`) and **SQLsmith**; record coverage over time with `cov.py` | L | The abstract promises an OSS-Fuzz comparison |
| N5 | New oracles in `oracle.py`: NoREC and TLP query generators over the seed schemas; wire into `sweep_oracle.py` | M | Logic bugs are our most likely finds |
| N6 | Campaign runner: long, resumable ASan runs with a time budget and periodic `triage` + `report` | M | Needed for any bug to turn up |
| N7 | Dockerfile or documented apt list so a clean machine builds in one command | S | Teammates and graders |

Deliverable: reproducible builds (v1.5.6 and `main`), baseline coverage numbers,
a faster driver, and the campaign runner.

## 5. Jahnvi — program analysis, coverage gaps, test data

| # | Task | Pts | Why |
|---|---|---|---|
| J1 | Choose the extraction approach: CodeQL database for DuckDB (heavy, may need a bigger machine) or a tree-sitter/clang-AST fallback | M | CodeQL may not be feasible on every laptop |
| J2 | **Core:** extract boundary constants, comparisons and risky sinks (`memcpy`, unchecked indexing, casts of sizes, `D_ASSERT`) from `src/optimizer`, `src/planner`, `src/execution`, `src/function`, `src/storage`; emit `static/hints.json` | L | Prompt material for the LLM |
| J3 | Extend `cov.py`: per-function coverage, rank uncovered regions, join with `hints.json` ("hint lines never executed") | M | This is the "directed" part |
| J4 | **Schema and data generator** (`src/gen/`): diverse tables (nested STRUCT/LIST/MAP, NULLs, 2047/2048/2049-row sizes, extreme values) as prompt context and fuzz setup | L | The LLM needs realistic schemas; many bugs sit at vector-size boundaries |
| J5 | Improve `extract_seeds.py`: expand `foreach` loops and `${type}` templating, measure how many statements survive and execute cleanly | M | We currently skip 1,454 of 3,413 test files |
| J6 | Mutation baseline: better mutators in `baseline.py` (grammar-aware swaps, nested expression wrapping) and a per-mutator effectiveness table | S | Stronger baseline makes the comparison honest |
| J7 | Evaluate hints: fraction in uncovered code, and how many generated queries reached them | S | RQ3 |

`hints.json` (one object per hint):

```json
{"file": "src/execution/...cpp", "line": 123, "kind": "magic_const|branch|sink",
 "value": "2048", "function": "Foo::Bar", "context": "<5 lines of source>"}
```

Deliverable: `static/extract_hints.py` + `hints.json`, the schema/data generator,
the improved coverage report, and an honest note on precision and limits.

## 6. Prabhnoor — LLM generation and the feedback loop

| # | Task | Pts | Why |
|---|---|---|---|
| P1 | `llm/client.py`: provider-agnostic client for a **local** model (OpenAI-compatible URL, e.g. Ollama), URL/model from env | S | Model runs on another machine; no paid API |
| P2 | **Core:** `llm/generate.py`: prompt builder (schema + uncovered region + hint + examples), batch SQL output, `EXPLAIN` validity check with one repair retry, save to `corpus/llm/<run>/` | L | Central contribution |
| P3 | Feedback loop: run generated SQL on the coverage build, merge, recompute uncovered regions, re-prompt; add `generate` stage to `fuzz.py` | L | "Directed" generation |
| P4 | Prompt strategy experiments: region-focused vs feature-focused, few-shot examples, temperature, batch size | M | Which prompts actually add coverage |
| P5 | Evaluation harness (`src/eval/`): equal budgets and seeds for (a) mutation baseline, (b) LLM only, (c) LLM + hints; coverage-over-time and valid-query-rate tables and plots | M | RQ1 and RQ2 |
| P6 | Paper integration: `HANDOFF.md` numbers table, figures, `make check`, anonymity and AI-disclosure checklist | M | Keeps the report consistent |
| P7 | Metrics logging: per-batch parse/bind/execute rates and coverage gain per query, in one shared JSONL schema used by `eval/` | S | Makes RQ2 measurable and comparable across runs |

Deliverable: a working `fuzz.py generate`, the evaluation scripts, and the
RQ1/RQ2 tables.

## 7. Paper sections (equal shares)

| Section | Lead | Also contributes |
|---|---|---|
| Introduction + motivating example (must be real) | Prabhnoor | all |
| Background + Related work: DBMS fuzzers (SQLsmith, SQLancer, OSS-Fuzz) | Niket | |
| Related work: static analysis for directed fuzzing | Jahnvi | |
| Related work: LLM-based fuzzing/test generation | Prabhnoor | |
| Approach / workflow figure | Jahnvi | Prabhnoor |
| Implementation + setup/reproducibility | Niket | |
| Evaluation: RQ1/RQ2 | Prabhnoor | |
| Evaluation: RQ3 (hints) | Jahnvi | |
| Evaluation: RQ4 (bugs, baselines) | Niket | |
| Threats to validity | each writes the threats of their own component | |
| Abstract / conclusion | rotates; final edit by all | |

Verify every citation yourself (Crossref/arXiv); no fabricated references.

## 8. Timeline (adjust to the real deadline)

| Week | Niket | Jahnvi | Prabhnoor |
|---|---|---|---|
| 1 (now) | N1, N2 | J1, J5 | P1, P2 (skeleton) |
| 2 | N3 | J2 | P2, P3 (skeleton) |
| 3 | N5, N7 | J4, J3 | P3, P4 |
| 4 | N4 | J6, J3 | P4, P5 |
| 5 | N6 campaigns, triage | J7 | P5 final runs |
| 6 | paper + data | paper + data | paper + data |

Also in week 1, everybody: run `fuzz.py all --limit 100` once and read
`results/REPORT.md`, so each person understands the whole pipeline and can
explain it in the final presentation.

## 9. Interfaces (do not change without telling the others)

| From -> To | Format |
|---|---|
| Static analysis -> LLM stage | `static/hints.json`, schema in section 5 |
| Schema generator -> LLM stage and fuzzing | SQL DDL files in `src/gen/out/*.sql` |
| Build -> everyone | `$FUZZ_WORK/build-asan/duckdb`, `$FUZZ_WORK/build-cov/duckdb`, `$FUZZ_WORK/build-main/duckdb` |
| LLM stage -> triage | SQL files under `$FUZZ_WORK/corpus/llm/<run>/*.sql` |
| Sweeps -> triage -> report | `results/*.jsonl` -> `results/triage/<id>/meta.json` -> `results/REPORT.md` |
