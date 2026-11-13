# `src/` — your code

Put **all your project code here** (tool, scripts, experiments, notebooks —
whatever your project produces).

Guidelines:

- Keep the code that produces every number in your paper in this folder, so a
  reader can reproduce your results.
- Add a short build/run note here (how to build, how to run, dependencies) —
  either extend this file or add your own `README`.
- Record exact versions of anything you depend on (compiler, tools, libraries)
  so results can be reproduced. These are the same versions you report in the
  paper's *Setup* subsection.
- If your project is an empirical study with no tool, put your analysis scripts
  and data-processing code here instead.

Do **not** commit large build outputs or binaries; commit the source and the
scripts that regenerate them.

---

## Project: directed LLM-guided fuzzing of DuckDB

Fuzzes DuckDB (pinned in `DUCKDB_VERSION`) with SQL queries, uses an
AddressSanitizer build to catch memory errors, differential oracles to catch
logic bugs, and llvm-cov coverage to steer query generation.

### Layout

| Path | Purpose |
|---|---|
| `build.sh` | Builds `build-asan/` (ASan+UBSan) and `build-cov/` (llvm-cov) DuckDB binaries |
| `setup.sh` | Clones pinned DuckDB, creates a venv, installs `requirements.txt` |
| `harness/runner.py` | Runs SQL on a build, classifies the outcome, dedupes by signature |
| `harness/oracle.py` | Differential (threads / optimizers / ordering) and TLP oracles |
| `harness/extract_seeds.py` | Converts DuckDB's sqllogictest files into plain SQL seeds |
| `harness/baseline.py` | Non-LLM mutation fuzzer and campaign loop (evaluation baseline) |
| `harness/sweep_seeds.py` | Runs every seed on the ASan build, tabulates outcomes |
| `harness/sweep_oracle.py` | Differential sweep: replays seeds under 5 configs, flags internal errors / error diffs / value mismatches |
| `harness/triage.py` | Auto-triage: find config, delta-debug SQL, minimise optimizer set, confirm on stock build, search upstream issues/PRs |
| `harness/fuzz.py` | **One-command pipeline** (seeds, sweeps, baseline, triage, report) |
| `coverage/cov.py` | Cumulative llvm-cov merge, summaries, uncovered-region report |
| `llm/` | LLM query generator (local model; planned) |
| `static/` | Static-analysis hints: constants, branch bounds, sinks (planned) |

### Setup and run

Requires: clang, lld, llvm (llvm-cov, llvm-profdata), cmake, ninja, ccache,
python3, and the GitHub CLI `gh` (logged in; used for the upstream duplicate
search). Tested: clang 21.1.8, cmake 4.2.3, Python 3.14. Builds need ~8 GB RAM.

```sh
export FUZZ_WORK=$HOME/fuzzwork      # optional; default is this directory
./setup.sh                           # clone DuckDB v1.5.6, create venv
./build.sh                           # ~1-2 h; asan then cov
$FUZZ_WORK/.venv/bin/python harness/fuzz.py all --baseline-minutes 30
```

`fuzz.py all` runs every stage unattended and writes `$FUZZ_WORK/results/REPORT.md`:

| Stage | What it does |
|---|---|
| `seeds` | extract DuckDB sqllogictests into plain SQL seeds |
| `sweep` | ASan crash sweep + differential sweep (5 configs) over all seeds |
| `baseline` | mutation fuzzing on the ASan build (`--baseline-minutes`) |
| `triage` | reproduce, minimise (statements and optimizer set), confirm on the stock build, search upstream issues and PRs |
| `report` | `REPORT.md`: candidate-new findings first, likely duplicates last, each with a minimal repro |

Stages can be run alone (`fuzz.py sweep`, `triage`, `report`, `status`); work is
incremental (`results/known.json`). Upstream verdicts are heuristic
(`no-match` / `similar-found` / `duplicate-likely`); confirm manually before
reporting anything.

Individual tools:

```sh
python harness/smoke_test.py
python harness/triage.py crash path/to/script.sql
python harness/baseline.py --build cov --minutes 30 --cov-every 25
python coverage/cov.py report src/optimizer src/execution
```

`FUZZ_WORK` holds everything large or regenerable (DuckDB source, builds,
seed corpus, run logs); none of it is committed. Confirmed, minimized
reproducers go in `crashes/`; summary numbers used in the paper go in
`paper/data/`.

See [`../docs/WORKPLAN.md`](../docs/WORKPLAN.md) for who does what and the task list.

## Reproducing the paper (DuckDB 2.0.0)

Tested on Ubuntu (WSL2), 8 hardware threads, 7.4 GB RAM, Python 3.14, DuckDB 2.0.0. Big files live outside the repo:

```bash
export FUZZ_WORK=$HOME/cs5187; PY=$FUZZ_WORK/.venv/bin/python
python3 -m venv $FUZZ_WORK/.venv && $PY -m pip install --only-binary=:all: duckdb==2.0.0
git clone https://github.com/duckdb/duckdb $FUZZ_WORK/duckdb && git -C $FUZZ_WORK/duckdb checkout v2.0.0   # source for rule cards / the LLM
```

| Method | What it is | Command |
|---|---|---|
| A classic | SQLsmith on an ASan build, differential sweeps, issue replay | `src/build.sh` (ASan build), then `$PY harness/fuzz.py all`, `$PY harness/campaign.py` |
| B features | trigger / window / join / bottom-up fuzzers | `$PY harness/fuzz_triggers.py`, `fuzz_windows.py`, `fuzz_joins.py`, `bottomup.py` |
| C hints + LLM | hints, feature cards, dossiers; LLM-written generators | `$PY harness/targeted_run.py --hours 12` (see `docs/RUNBOOK_TARGETED_RUN.md`) |
| D rule loop | rule cards + hypothesis-first generators + engine judge | see below and `docs/HUNT_LOOP.md` |

Method D (needs Claude Code logged in; the LLM gets read-only tools on the DuckDB source only):

```bash
$PY src/harness/rule_targets.py build                          # 49 rule cards -> $FUZZ_WORK/results/targeted/rule_cards/
$PY src/harness/controls/check_controls.py --rediscovery       # must print CONTROLS PASSED
src/harness/hunt_start.sh --run hunt1 --hours 8 --max-llm-calls 40   # detached, resumable; results/hunt/hunt1/
```

Check one reproducer: `$PY src/harness/verify_finding.py --diff path/to/script.sql`.

Paper numbers and tables: `FUZZ_WORK=$HOME/cs5187 $PY paper/scripts/collect_data.py && $PY paper/scripts/collect_extra.py`
(writes `paper/data/*` and `paper/tables/tab_issues.tex`, `tab_hunt1.tex`; needs `gh` for the live issue state).
