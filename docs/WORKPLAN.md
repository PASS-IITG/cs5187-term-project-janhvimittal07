# Work plan — Directed LLM-guided fuzzing of DuckDB

Status as of 2026-10-10. Read `src/README.md` first for how to build and run.

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

## 3. Niket — build system, sanitizers, driver

Owns: `src/build.sh`, `src/setup.sh`, `src/harness/runner.py`,
`src/harness/smoke_test.py`.

| # | Task | Why |
|---|---|---|
| N1 | Review and correct the ASan/UBSan and coverage build flags in `build.sh` (are assertions, `-fno-omit-frame-pointer`, `ASAN_OPTIONS`/`UBSAN_OPTIONS`, extension set right?) | Everything runs on these binaries |
| N2 | Add a build of DuckDB `main` at a pinned commit (`DUCKDB_COMMIT`), so findings are checked against the latest code, not only v1.5.6 | Two of our "bugs" were already fixed on main |
| N3 | Build an in-process driver (C++ or the C API) that runs many queries per process instead of one CLI process per script | Process start-up dominates run time; needed for long campaigns |
| N4 | Make the coverage build and ASan build produce comparable results (same flags except instrumentation); document build time and RAM | Reproducibility section of the paper |
| N5 | Reproduce the **OSS-Fuzz baseline** named in the proposal: find out what DuckDB's OSS-Fuzz targets are (google/oss-fuzz `projects/duckdb`), build and run them for the same time budget, and record coverage | The abstract promises a comparison against OSS-Fuzz |
| N6 | Add a `Dockerfile` or documented apt list so a clean machine builds in one command | Teammates and graders |

Deliverable: a documented, reproducible `./build.sh` for both builds + `main`,
and baseline coverage numbers from OSS-Fuzz.

## 4. Jahnvi — static analysis (CodeQL / AST)

Owns: `src/static/` (currently empty).

| # | Task | Why |
|---|---|---|
| J1 | Decide extraction approach: CodeQL database for DuckDB (heavy; may need more than 8 GB RAM, build on a lab machine) or a tree-sitter/clang-AST script as a fallback | CodeQL may not be feasible on every laptop |
| J2 | Extract **boundary constants and comparisons** (e.g. `STANDARD_VECTOR_SIZE` = 2048, `INT32_MAX`, list/array size limits, `if (x > K)`) with file, line and surrounding code | Prompt material for the LLM |
| J3 | Extract **risky sinks**: `memcpy`/`memmove`, unchecked indexing, `reinterpret_cast`, `static_cast` of sizes, `D_ASSERT`s | Where memory bugs live |
| J4 | Restrict to the directories that matter (`src/optimizer`, `src/planner`, `src/execution`, `src/function`, `src/storage`) | Keeps hints useful |
| J5 | Emit `static/hints.json` using the schema below | Contract with the LLM stage |
| J6 | Join hints with uncovered regions (`coverage/cov.py` `uncovered_regions()`): rank hints whose lines are *never executed* | This is the "directed" part of the project |
| J7 | Evaluate: how many hints are in uncovered code, and how many queries reached them | RQ3 in the paper |

`hints.json` (one object per hint):

```json
{"file": "src/execution/...cpp", "line": 123, "kind": "magic_const|branch|sink",
 "value": "2048", "function": "Foo::Bar", "context": "<5 lines of source>"}
```

Deliverable: `static/extract_hints.py` + `static/hints.json` + a short note on
precision and limits (for Threats to Validity).

## 5. Prabhnoor — LLM orchestration, coverage feedback, campaigns, triage

Owns: `src/llm/` (to write), `src/coverage/cov.py`, `src/harness/baseline.py`,
`src/harness/oracle.py`, `src/harness/triage.py`, `src/harness/fuzz.py`,
`src/harness/sweep_*.py`.

| # | Task | Why |
|---|---|---|
| P1 | `llm/client.py`: provider-agnostic client for a **local** model (OpenAI-compatible URL, e.g. Ollama), URL and model from env/config | Local model runs on another machine; no paid API |
| P2 | `llm/generate.py`: prompt = schema DDL + uncovered code region (+ hint) + examples; output = batch of SQL; `EXPLAIN` validity check with one repair retry; save to `corpus/llm/<run>/` | Core contribution |
| P3 | Coverage feedback loop: run generated SQL on the coverage build, merge with `cov.py`, recompute uncovered regions, re-prompt | "Directed" generation |
| P4 | Per-optimizer fuzz mode: run generated queries under each single-optimizer disable + TLP/NoREC-style metamorphic checks | Upstream's CI mostly replays existing tests; novel queries here are our best shot |
| P5 | Campaign runner: long unattended runs on the ASan build, resumable, with time/budget logging | Needed for any bug to turn up |
| P6 | Evaluation harness: equal time budget and seeds for (a) mutation baseline, (b) LLM only, (c) LLM + hints; log coverage over time and valid-query rate | RQ1–RQ3 |
| P7 | Triage upgrades: statement-level text minimisation, "test on main" check, GitHub rate-limit handling | Cuts manual work |

Deliverable: `fuzz.py` stage `generate` that plugs into the existing
triage/report path, plus the evaluation tables in `paper/data/`.

## 6. Shared tasks

| Task | Who |
|---|---|
| Fill `paper/HANDOFF.md` (venue, deadline, numbers table) | all, each owns their numbers |
| Motivating example (must be real, exact numbers) | Prabhnoor, using a confirmed finding or the coverage-gap example |
| Related work (verify every citation) | Jahnvi (static analysis, directed fuzzing), Prabhnoor (LLM fuzzing), Niket (DBMS fuzzers: SQLsmith, SQLancer, OSS-Fuzz) |
| Threats to validity | all |
| Weekly 30-minute sync; re-check upstream for duplicates before any report | all |

## 7. Suggested order (6 weeks, adjust to the real deadline)

| Week | Niket | Jahnvi | Prabhnoor |
|---|---|---|---|
| 1 (now) | N1, N2 | J1, J2 | P1, P2 skeleton |
| 2 | N3 | J3, J4, J5 | P2, P3 |
| 3 | N4, N5 | J6 | P4, P6 baseline numbers |
| 4 | N5 results, N6 | J7 | P5 campaigns |
| 5 | triage and report any finding | evaluation text | P6 final runs, P7 |
| 6 | paper | paper | paper, data to `paper/data/` |

## 8. Interfaces (do not change without telling the others)

| From → To | Format |
|---|---|
| Static analysis → LLM stage | `static/hints.json`, schema in section 4 |
| Build → everyone | `$FUZZ_WORK/build-asan/duckdb`, `$FUZZ_WORK/build-cov/duckdb` (CLI binaries) |
| LLM stage → triage | SQL files under `$FUZZ_WORK/corpus/llm/<run>/*.sql` |
| Sweeps → triage → report | `results/*.jsonl` → `results/triage/<id>/meta.json` → `results/REPORT.md` |
