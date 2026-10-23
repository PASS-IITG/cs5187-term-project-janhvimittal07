# Runbook: a targeted bug-hunting run on DuckDB 2.0.0

**Why a plain "find bugs for 12 hours" run is futile.** On the released 2.0.0 we ran ~700,000 generic queries under
ASan/UBSan and verification (SQLsmith-style, DuckDB's own test seeds and their mutants, mined bug reproducers, all
function calls with edge values, NoREC/TLP oracles): every crash was already reported upstream. Upstream gets ~30
new issues a day from people running the same kind of fuzzing, mostly on joins, subqueries and storage. The one new bug
(duckdb/duckdb#27759) came from aiming at a **new 2.0 feature nobody covers** (triggers) with a **purpose-built generator
and oracle**. So: target new code, use oracles, verify everything.

## 0. Rules (these decide whether a result counts)
1. **Version gate.** Everything runs on the released **v2.0.0** (`source_id d54cbbe3df`). `python src/harness/preflight.py`
   must print `preflight ok` before every run. A run on another build proves nothing about 2.0.0.
2. **A finding must be reproduced alone** in a fresh process on the released wheel (and ideally the ASan build), with the
   minimal SQL and the output saved. Crashes seen only under memory caps / timeouts / many threads are artifacts.
3. **Check upstream before believing it is new**: open AND closed issues, and open PRs. See section 5.
4. **Do not file anything upstream** without the team agreeing. One person files, from their own GitHub account.
5. Numbers for the paper come from real runs only: record queries run, wall time, build/version, findings by status.

## 1. One-time setup (per machine)
```bash
git pull                                              # this repo, main
export FUZZ_WORK=$HOME/cs5187                         # heavy artifacts live here, not in git
cd $FUZZ_WORK && git clone https://github.com/duckdb/duckdb.git duckdb   # or reuse; then:
git -C $FUZZ_WORK/duckdb fetch --tags && git -C $FUZZ_WORK/duckdb checkout v2.0.0
python3 -m venv $FUZZ_WORK/.venv     && $FUZZ_WORK/.venv/bin/pip install -r src/requirements.txt   # duckdb==2.0.0
python3 -m venv $FUZZ_WORK/.venv-dev && $FUZZ_WORK/.venv-dev/bin/pip install duckdb==2.0.0         # generators' workers
# sanitizer build from the v2.0.0 source (about an hour; 3 jobs, ~7 GB RAM; do not run other heavy jobs meanwhile)
FUZZ_WORK=$FUZZ_WORK JOBS=3 bash src/build.sh asan
FUZZ_WORK=$FUZZ_WORK make -C src/driver asan            # fast in-process batch driver (Linux and macOS)
$FUZZ_WORK/.venv/bin/python src/harness/preflight.py    # MUST say "preflight ok"
```
Always run Python tools with `$FUZZ_WORK/.venv/bin/python` and `FUZZ_WORK` exported. Run from an empty scratch directory
(`mkdir -p $FUZZ_WORK/work-scratch && cd $FUZZ_WORK/work-scratch`) because some seeds write files to the cwd.

## 2. Build the target pack (data, not committed; ~5 minutes)
```bash
cd src
python harness/make_added_seeds.py --old v1.5.6 --new v2.0.0     # tests ADDED in 2.0 -> corpus/seeds-v200-added (789 scripts)
pip install tree-sitter tree-sitter-cpp                          # once, into .venv, for the hint extractor
python static/extract_hints.py --out $FUZZ_WORK/results/hints-v200.json --subsystems src/optimizer src/planner \
    src/execution src/function src/storage src/parser src/catalog src/main src/common extension/core_functions \
    extension/json extension/parquet
python static/new_code_hints.py --old v1.5.6 --new v2.0.0 --hints $FUZZ_WORK/results/hints-v200.json \
    --out $FUZZ_WORK/results/hints-v200-new.json                 # 27k hints inside code changed since 1.5.6
python llm/feature_cards.py build                                # valid 2.0 example scripts per feature
```

## 3. Runs, most valuable first
Each tool prints findings as it goes and writes `results/<name>/hits-*.jsonl`. "0 findings" is a normal outcome; do more
seeds (`--seed N`) and move on. Wheel-based tools are light; run **one ASan job at a time**.

| # | What | Command (from `src/`) | Time | Why |
|---|---|---|---|---|
| A | **Trigger fuzzer** (new feature, 3 oracles) | `python harness/fuzz_triggers.py --n 3000 --seed 1` (try seeds 1..20) | ~11 min/seed | found #27759; oracles: hand-emulation of trigger bodies, in-memory vs reopened file DB, INTERNAL/crash. Use `--keep-known` only to re-see #27759 |
| B | Bottom-up + window generators with the `delim_join_as_cte` on/off comparison | `python harness/bottomup.py --n 8000 --seed 1`<br>`python harness/fuzz_windows.py --n 8000 --seed 1` | ~2-4 min/seed | the delim-join→CTE rewrite is new and already buggy |
| C | Join oracle (NATURAL JOIN vs INTERSECT ALL/EXCEPT ALL, mixed key types, PRIMARY KEY) | `python harness/fuzz_joins.py --n 10000 --seed 1` | ~20 min/seed | JoinEquiv oracle |
| D | Differential + NoREC/TLP sweep over the **added** tests | `python harness/sweep_oracle.py --seeds $FUZZ_WORK/corpus/seeds-v200-added --out $FUZZ_WORK/results/sweep-added.jsonl --metamorphic --workers 2` | ~10 min | new configs: `approx_join_order`, `verify` |
| E | ASan replay of the added tests, then of their mutants | `python harness/asan_replay.py --sql-dir $FUZZ_WORK/corpus/seeds-v200-added --out $FUZZ_WORK/results/asan-added`<br>`python harness/mutate_corpus.py --seeds $FUZZ_WORK/corpus/seeds-v200-added --n 6000 --out $FUZZ_WORK/corpus/mutants/added --seed 1`<br>`python harness/asan_replay.py --sql-dir $FUZZ_WORK/corpus/mutants/added --out $FUZZ_WORK/results/asan-added-mut` | ~30 / ~60 min | memory bugs and assertions in new code (see ASan options below) |
| F | **LLM step aimed at new code** (needs a model endpoint: `LLM_BASE_URL`, `LLM_MODEL`, optional `LLM_API_KEY` env vars) | `python llm/generate.py --run new1 --targets hints --hints $FUZZ_WORK/results/hints-v200-new.json --cards $FUZZ_WORK/results/feature-cards.json --n-prompts 200`<br>then `python harness/asan_replay.py --sql-dir $FUZZ_WORK/corpus/llm/new1 --out $FUZZ_WORK/results/asan-llm-new1` | depends on model | the project's core idea; keep an `--targets none` control run for the paper |

Skip (already done, nothing new): generic SQLsmith campaigns, replaying all function calls with edge values, the generic
seed corpus, NoREC/TLP on old seeds.

**ASan options** (never use `ulimit -v` / RLIMIT_AS with ASan: it reserves terabytes of address space and breaks):
```bash
export ASAN_OPTIONS="detect_leaks=0:symbolize=1:abort_on_error=0:hard_rss_limit_mb=2500:allocator_may_return_null=1"
export UBSAN_OPTIONS="print_stacktrace=1:halt_on_error=1"
# Linux with systemd: cap the whole process group too
systemd-run --user --scope -p MemoryHigh=3G -p MemoryMax=4G nice -n 10 <command>
```
If the DuckDB shell is invoked by an AI tool, pass `-no-agent` (or unset `CLAUDECODE`/`AI_AGENT`): in "agent mode" it
prints errors as JSON and `INTERNAL Error` is no longer recognised.

## 4. Triage (every hit, no exceptions)
1. Read `hits.jsonl`; ignore `echo artifact`, `resource artifact`, `NOT reproducible alone`.
2. Save the minimal SQL to a file. Run it **alone** in a fresh process on (a) the 2.0.0 wheel, (b) `build-asan/duckdb -no-agent`.
3. Reduce it by hand to the smallest script; try `SET threads=1`, `SET disabled_optimizers='...'`, and the new settings
   (`delim_join_as_cte=false`) to locate the cause.
4. Compare with `pip install --pre --target /tmp/x duckdb` (the 2.0.1 development snapshot) to see if it is already fixed.
5. Search upstream: `gh search issues --repo duckdb/duckdb "<error text>" --state all`, then `gh search prs ...`, and
   skim `gh issue list --repo duckdb/duckdb --limit 100 --json number,title`. Space the searches ~8 s apart (rate limits).
6. Is it only a *sanitizer/assertion* hit with correct results in the release build? Then it is a lower-severity
   "assertion builds only" report; say so.

**Already known - do not re-report:** #27446 (`exprs.size() > 0`, correlated `HAVING true`), #27448 (`dense_rank() OVER ()`
over an anti join, segfault), #27450 (`GetPartitioningSpaceRequirement: tuple width should not be 0`), #27631 (two MERGE
trigger bodies, "Failed to bind column reference"), #27759 (ours: row trigger cascading into a table with a BEFORE trigger,
`delim_join_as_cte`), plus the many join/subquery INTERNAL errors from #27716-#27747. Always re-search; this list ages fast.

## 5. Brief for an AI agent (Antigravity / Gemini / Claude) - paste as the task
```
You are assisting a DuckDB 2.0.0 bug hunt. Work only inside $FUZZ_WORK and the repo at ~/term-project.
1. Run `python src/harness/preflight.py`. If it does not print "preflight ok", stop and report why.
2. Execute runbook sections 2 and 3 in order A, B, C, D, E (F only if an LLM endpoint is configured). One ASan job at a time.
   Log the exact command, start/end time and the tool's summary line for every run in results/RUN_LOG.md.
3. For every hit follow section 4. Never state that something is a bug unless you have: the minimal SQL file, its output on
   the 2.0.0 wheel AND the ASan build, and the upstream search terms you tried with their results.
4. Do not install or delete anything outside $FUZZ_WORK, do not push to git, do not post to GitHub, do not change settings.
5. At the end write results/RUN_REPORT.md: version line; counts per run (queries, seeds, wall time); a table of verified
   findings (id, SQL file, version(s) affected, upstream status); a separate list of rejected hits with the reason.
   If there are no verified findings, say exactly that - an honest zero is the expected outcome of most runs.
```

## 6. Reporting
A verified, upstream-checked finding goes to the team first. One person files it, using `docs/upstream-drafts/` as the
template (title, minimal reproducer + output in separate code blocks, versions incl. source id, workaround, stack trace
from the ASan build, related-but-different issues). Record the issue number in the notes and the paper.
