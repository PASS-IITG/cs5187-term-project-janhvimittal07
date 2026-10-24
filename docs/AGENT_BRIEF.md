# Task brief for an AI coding agent (Gemini / Antigravity / Claude / ...): DuckDB 2.0.0 targeted bug hunt

You are running a long, mostly automated bug hunt against the **released DuckDB v2.0.0** for a university project. The
repository does the work; **your job is to start it, keep it healthy, and report honestly.** Read this whole file first.

## Ground truth you must not forget
* A generic "find bugs" run on 2.0.0 finds nothing new: ~700,000 queries under sanitizers already produced only bugs that
  upstream had reported. Zero verified findings is the *normal* outcome. Reporting zero honestly is a success.
  Reporting a fake or already-known bug is a failure.
* The repo's scripts target code that is **new in 2.0** (triggers, the delim-join-to-CTE rewrite, new optimizers, ...)
  and verify every hit mechanically. Do not replace them with your own ad-hoc fuzzing; your own SQL goes through Phase B, which feeds the same verification.

## Which model, and how "finished" is decided
* Use the strongest model your environment offers for Phase B (for example Claude Opus 5.5 or Fable 5.1 if your tool lets you pick them; check the
  picker, do not assume). Writing generators is the hard, creative part. Record the model in every generator's `META["author_model"]`.
* **You may only say the work is finished when `PY src/harness/targeted_run.py --audit` prints `AUDIT PASSED`.** If it prints `AUDIT FAILED`, paste the
  `[FAIL]` lines, say what is missing, and fix them or stop and report them. The audit checks: preflight; at least 12 generators that are
  `Quality: ok`, ran >= 3000 cases and name their author model; a fresh RUN_REPORT; FINAL_SUMMARY and AGENT_NOTES; at least 20 fix dossiers worked;
  coverage for BOTH A1 (generic baseline) and A4 from the same build; every candidate in the report discussed. A summary that says "all done" without
  a passing audit is wrong, whatever the numbers look like.
  The audit recomputes each generator's quality from its stored counts, ties the metrics to the generator file (the hash must match), needs >= 6 distinct
  targets, a verdict next to each dossier id, and coverage arms that differ. It stops lazy over-claiming; it cannot stop deliberate forgery, so a human still
  reads the generators, `metrics.json` and the reduced reproducers before anything goes into the paper.
* The report separates **rediscoveries** (an issue we already know; never a finding), **sibling candidates** (the error text of a known issue but a
  different shape: read these first, they may be new), upstream matches, fixed-in-snapshot, and artifacts. Do not call a rediscovery a bug found.

## Hard rules
1. **Never** run `git push`, `git commit`, `gh issue create`, `gh pr ...`, `gh api` with POST/PATCH/DELETE, or anything that
   posts to the internet. Read-only `gh search` / `gh issue view` is allowed (and used by the scripts).
2. **Never** change files under `src/` or `docs/`. If a script is broken, stop that phase, log it, and continue with the next.
3. Work only inside `$FUZZ_WORK` (the heavy-artifacts directory) and read-only in the repo. No `sudo`. No installs outside
   the two virtualenvs `$FUZZ_WORK/.venv` and `$FUZZ_WORK/.venv-dev` and `$FUZZ_WORK/dev-snapshot`.
4. **Never state that something is a bug** unless `RUN_REPORT.md` lists it as CANDIDATE NEW *and* you reproduced it yourself
   (step 5). Never invent SQL, outputs, version strings or issue numbers; copy them from files.
5. Run **one heavy job at a time**. Never use `ulimit -v` with the sanitizer build. If free RAM < 1.5 GB or free disk
   < 5 GB, stop the run (`pkill -f targeted_run.py`), write what happened to `FINAL_SUMMARY.md`, and finish.
6. Do not poll more often than every 30 minutes while the run is going. Between checks, do nothing.

## Steps
**0. Variables.** `export FUZZ_WORK=<heavy-artifacts dir, e.g. $HOME/cs5187>`; `cd <repo>`; `git pull` is allowed.
Use `$FUZZ_WORK/.venv/bin/python` for every Python command below (call it `PY`).

**1. Preflight.** `PY src/harness/preflight.py`. It must end with `preflight ok`.
If it does not, do exactly what each `[FAIL]` line says (the lines contain the commands) and re-run it. The ASan build
(`FUZZ_WORK=$FUZZ_WORK JOBS=3 bash src/build.sh asan`, about 1 hour) and the driver (`make -C src/driver asan`) are the
slow parts: start them first, in the background, and wait. If something still fails after one attempt per item, stop and
write `FINAL_SUMMARY.md` with the failing lines. **Do not run the hunt on a failing preflight.**

**2. Smoke test (about 5 minutes).** `mkdir -p $FUZZ_WORK/work-scratch && cd $FUZZ_WORK/work-scratch`, then
`PY <repo>/src/harness/targeted_run.py --smoke --fresh`. It must finish with `DONE. Report: ...RUN_REPORT.md`. If a phase
fails, read `results/targeted/logs/<unit>.log`, fix the *environment* (never the repo), and rerun. If it cannot be made to
pass, stop and report.

**3. The real run.** From the same scratch directory:
```bash
nohup PY <repo>/src/harness/targeted_run.py --hours 12 --fresh > $FUZZ_WORK/results/targeted-main.log 2>&1 &
```
Add `--llm` only if `LLM_BASE_URL` and `LLM_MODEL` are set and the endpoint answers (`curl $LLM_BASE_URL/models`).
The run is resumable: if the process dies, run the same command *without* `--fresh`. Every 30+ minutes read only
`tail -n 15 $FUZZ_WORK/results/targeted/RUN_LOG.md`. A unit with rc=0 or rc=-15 (time box) is fine. If three units in a
row fail, stop and report.

**4. Read the report** (`$FUZZ_WORK/results/targeted/RUN_REPORT.md`) when the process has exited. Sections:
* **CANDIDATE NEW** - reproduces alone, not fixed in the newer snapshot, nothing found upstream.
* **Not searchable automatically** - reproduces but has no error text/stack frames to search for. Treat like CANDIDATE NEW.
* **Reproduces, upstream has matching reports** - probably known; check the listed issues.
* **Already fixed in the development snapshot**, **Artifacts**, and **Needs human review** (result mismatches; many are by design).

**5. For every CANDIDATE NEW / not-searchable item** (max 10, most interesting first):
1. Open its script (`candidates/candNNN.sql`) and reduce it by hand to the smallest script that still fails
   (remove statements/columns one at a time; re-run with `PY src/harness/verify_finding.py <file>`).
2. Re-run the final reduced script on the wheel and with `$FUZZ_WORK/build-asan/duckdb -no-agent` and record the outputs.
3. Search upstream yourself: `gh search issues --repo duckdb/duckdb "<error text without numbers>" --state all` and the
   same with `prs`, plus 2-3 shorter phrases and the function/feature names in the SQL. Wait ~8 s between searches.
4. Decide: *new*, *known (issue #...)*, or *artifact*, with the evidence.

**6. Write `$FUZZ_WORK/results/targeted/FINAL_SUMMARY.md`** with exactly these parts:
```
# Final summary
Version line (copy the preflight output) | start/end time | hours actually run
Queries/cases/seeds executed per phase (copy from RUN_LOG.md)  <- needed for the paper
Verified findings: table (id | reduced SQL file | versions affected | upstream status | evidence file)   [may be empty]
Rejected hits: one line each with the reason
Problems met (broken phases, environment fixes you made)
What you did NOT do (anything skipped)
```
If there are no verified findings, say: "No verified new findings." and stop. Do not pad the report.

## Phase B - you are the fuzzer's brain: write GENERATORS, not queries (this is the core of the project)
The scripts fuzz mechanically. What they cannot do is read the new C++ and decide what to attack. You can. But **do not write
SQL by hand**: one hand-written query is one test case. Write small *generator programs*: one program yields thousands of
test cases, and a generator like that (`fuzz_triggers.py`, hand-written for one new feature) is what found duckdb/duckdb#27759.
The engine, not you, judges every generator (`harness/gen_runner.py`); nothing you say about your own work is trusted.

**The loop (per target; 3-4 iterations, then scale up):**
1. *Choose a target* - something that is new in 2.0 and has a switch you can flip: one of the new optimizers
   (`PY -c "import sys;sys.path.insert(0,'src/harness');import oracle;print(oracle.NEW_OPTIMIZERS)"` with `FUZZ_WORK` set), or the setting
   `delim_join_as_cte`; features without a switch (triggers, `variant_*`/`json_*` functions, aggregate-state export, `tuple`,
   `NEAREST` joins, `DEFAULT` binding in INSERT/UPDATE/MERGE) are fine too, then only validity and plan variety are measured.
   Pairs of features are the best targets (the one real bug so far was an interaction: row trigger x BEFORE trigger x delim-join CTE).
2. *Read the code, on demand* (do not load everything): `$FUZZ_WORK/duckdb/<file>`, starting with the files that have the most
   changed code (`results/hints-v200-new.json`, see the runbook for how to make it), and the matching feature card in
   `results/feature-cards.json` and tests in `corpus/seeds-v200-added/` for the exact syntax. Note the branches, limits and special cases.
3. *Write the generator* into `$FUZZ_WORK/generators/<name>.py` (never into the repo). Start from the template
   `src/gen/llm_generators/delim_cte_subqueries.py` and keep its contract: `META` (with `"author_model"` set to the name of the
   model that wrote it) and a pure `generate(rng)` that returns `{"setup": [...], "queries": [...]}`. Only `random`, `itertools`,
   `string`, `math`, `textwrap`, `functools`, `collections` may be imported; no files, no network, no extensions; the checker rejects the rest.
4. *Run the judge:* `PY src/harness/gen_runner.py check $FUZZ_WORK/generators/<name>.py` (30 cases, about a minute). It prints
   engine-measured feedback: valid-query rate, **reach rate** (do the plan's operators or their expressions change when the target switch is flipped, i.e. does the
   query really exercise the target code), distinct physical-plan operator trees (read from `EXPLAIN (FORMAT JSON)`), the most common SQL errors, and examples that did / did not reach.
5. *Repair using that feedback only.* Fix the SQL errors it lists, copy the shapes that reach, change what the missing ones lack, add
   NULLs / empty tables / duplicates / 2048-row tables / deeper nesting. Repeat steps 4-5 until the verdict is `Quality: ok` AND valid rate >= 85% AND (if there is a switch)
   reach rate >= 50% (the hard floor is 30%), or after 4 rounds, whichever comes first. **`Quality: LOW DIVERSITY` means the generator emits the same few templates with
   different constants (e.g. exactly 8 queries per case, 100% valid, a dozen plan shapes): it is not a test, it does not count, and the report lists it
   as such. A good generator makes random STRUCTURAL choices (which clause, which join type, nesting depth, how many subqueries, which functions) and
   gets a few percent invalid queries.** The pilot `delim_cte_subqueries.py` is the bar: ~200 templates per 30 cases, hundreds of plan shapes. Do not game the numbers: trivial queries that are always valid but never
   reach the target are useless, and the report will show they found nothing.
6. *Scale:* `PY src/harness/gen_runner.py run $FUZZ_WORK/generators/<name>.py --n 3000 --seed S` for several seeds `S`. Every case runs under
   five configurations on fresh connections (default, `delim_join_as_cte=false`, the target switch off, `threads=1`, `enable_verification`); it
   flags INTERNAL errors, crashes and results that differ between configurations. Hits land in `results/targeted/gens/<name>/hits.jsonl`.
7. *Memory bugs:* for the best generators write cases out and replay them on the sanitizer build (one heavy job at a time):
   `PY src/harness/gen_runner.py dump $FUZZ_WORK/generators/<name>.py --n 300 --seed S --out $FUZZ_WORK/corpus/llm/agent` then
   `PY src/harness/asan_replay.py --sql-dir $FUZZ_WORK/corpus/llm/agent --out $FUZZ_WORK/results/targeted/asan-agent`.
   Finally `PY src/harness/targeted_run.py --report-only` verifies every hit alone on all builds and searches upstream.

**Second track - fix dossiers (variant hunting).** Run `PY src/harness/fix_dossiers.py --refs origin/main --limit 80`. Each file in
`results/targeted/dossiers/` is a bug that upstream FIXED after 2.0.0 (so it exists in 2.0.0 and is known; never report it) with the
fix diff and the reproducer from its test. Your job: say what mistake the old code made, search the source for other sites making the
same mistake, and write a generator or reproducer for each site. Run `harness/verify_finding.py <file> --dev-site $FUZZ_WORK/dev-snapshot`
(`pip install --pre --no-deps --target $FUZZ_WORK/dev-snapshot duckdb` first): **fails on 2.0.0 AND on the newer snapshot = sibling /
incomplete-fix candidate**; fails only on 2.0.0 = just the original bug. `results/targeted/known_fixed.json` lists every fix commit since the
tag: use it to check "is this already fixed?" before spending time.

**Rules specific to this phase**
* Only engine-measured numbers count. Never write "this generator reaches the optimizer" - show the `metrics.json`.
* Record in `AGENT_NOTES.md`, per generator: target, iterations, valid/reach rate per iteration, model used, hits. Negative results too.
* Real findings go through `PY src/harness/targeted_run.py --report-only` (verifies alone on every build, searches upstream). Do not
  describe anything as a bug before that report lists it, and then only as a *candidate*.
* Aim for at least 12 generators that pass the quality check (the new optimizers that can be targeted from plain SQL, the dependent-join rewrite, and feature
  interactions; `remote_pushdown` and `partitioned_execution` need remote / partitioned data and `aggregate_function_rewriter` is only a rename of `sum_rewriter`,
  so they may be skipped), each run for >= 3000 cases, and 20 dossiers worked (name each dossier id in AGENT_NOTES.md with the result).

## Phase C - coverage numbers for the paper (needs the coverage build; ask the human before starting the 1-hour build)
The proposal promised "coverage improvements". Measure the coverage of the **lines added or changed since 1.5.6** (SQL-reachable code only)
for each arm, on a build of the SAME release. A stale build gives silently wrong numbers: `cov.py` refuses to run on one.
1. Build, with no other heavy job running: `rm -rf $FUZZ_WORK/build-cov && FUZZ_WORK=$FUZZ_WORK JOBS=3 bash src/build.sh cov` (about 1 hour).
   `PY src/harness/preflight.py` must show `[PASS] cov`.
2. One work directory per arm; scripts are replayed in a scratch directory, never in the DuckDB source tree:
   | arm | what to replay | command |
   |---|---|---|
   | A1 generic | the 2.0 test corpus `corpus/seeds-v200` (plus mutants if you ran them) | `PY src/coverage/cov.py run-dir $FUZZ_WORK/corpus/seeds-v200 --workdir $FUZZ_WORK/results/cov-A1` |
   | A3 LLM unguided | the output of `llm/generate.py --targets none --run unguided` | `... run-dir $FUZZ_WORK/corpus/llm/unguided --workdir $FUZZ_WORK/results/cov-A3` |
   | A4 LLM directed | generators dumped with `gen_runner.py dump ... --n 300` (all of them into one directory) plus the reproducers from dossiers | `... run-dir $FUZZ_WORK/corpus/llm/agent --workdir $FUZZ_WORK/results/cov-A4` |
   The hand-written targeted tools (A2) produce SQL in-process; their coverage is not measured unless you add a dump option.
3. `PY src/coverage/cov.py changed --workdir $FUZZ_WORK/results/cov-A4 --json $FUZZ_WORK/results/cov-A4/changed.json` prints the
   percentage of changed executable lines covered, per subsystem, and the files with the most uncovered lines (use that list as the
   next generator targets: it is the real coverage-guided loop). Do the same for A1 and A3 and report all numbers side by side.
4. `PY src/coverage/cov.py compare A1=$FUZZ_WORK/results/cov-A1/changed.json A4=$FUZZ_WORK/results/cov-A4/changed.json` prints the arms side by side with the
   difference to the baseline, and refuses to compare arms from different builds or without A1. Coverage must be recomputed after the generators are rewritten.
Report in `FINAL_SUMMARY.md`: build id, number of scripts per arm, covered/total changed lines per arm. Do not round or drop an arm that looks bad.

## Known upstream bugs (never re-report)
#27446, #27448, #27450, #27631, #27759, and the join/subquery INTERNAL errors in #27716-#27747. The report step also
searches upstream, but always check yourself: new issues appear at ~30 per day.
