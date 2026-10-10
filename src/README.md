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
| `coverage/cov.py` | Cumulative llvm-cov merge, summaries, uncovered-region report |
| `llm/` | LLM query generator (local model; planned) |
| `static/` | Static-analysis hints: constants, branch bounds, sinks (planned) |

### Setup and run

Requires: clang, lld, llvm (llvm-cov, llvm-profdata), cmake, ninja, ccache,
python3 (tested: clang 21.1.8, cmake 4.2.3, Python 3.14). The builds need
about 8 GB RAM.

```sh
export FUZZ_WORK=$HOME/fuzzwork      # optional; default is this directory
./setup.sh                           # clone DuckDB v1.5.6, create venv
./build.sh                           # ~1-2 h; asan then cov
$FUZZ_WORK/.venv/bin/python harness/extract_seeds.py
$FUZZ_WORK/.venv/bin/python harness/smoke_test.py
$FUZZ_WORK/.venv/bin/python harness/baseline.py --build asan --minutes 30
$FUZZ_WORK/.venv/bin/python harness/baseline.py --build cov --minutes 30 --cov-every 25
$FUZZ_WORK/.venv/bin/python coverage/cov.py report src/optimizer src/execution
```

`FUZZ_WORK` holds everything large or regenerable (DuckDB source, builds,
seed corpus, run logs); none of it is committed. Confirmed, minimized
reproducers go in `crashes/`; summary numbers used in the paper go in
`paper/data/`.
