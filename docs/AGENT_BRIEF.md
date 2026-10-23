# Task brief for an AI coding agent (Gemini / Antigravity / Claude / ...): DuckDB 2.0.0 targeted bug hunt

You are running a long, mostly automated bug hunt against the **released DuckDB v2.0.0** for a university project. The
repository does the work; **your job is to start it, keep it healthy, and report honestly.** Read this whole file first.

## Ground truth you must not forget
* A generic "find bugs" run on 2.0.0 finds nothing new: ~700,000 queries under sanitizers already produced only bugs that
  upstream had reported. Zero verified findings is the *normal* outcome. Reporting zero honestly is a success.
  Reporting a fake or already-known bug is a failure.
* The repo's scripts target code that is **new in 2.0** (triggers, the delim-join-to-CTE rewrite, new optimizers, ...)
  and verify every hit mechanically. Do not replace them with your own ad-hoc fuzzing.

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

## Known upstream bugs (never re-report)
#27446, #27448, #27450, #27631, #27759, and the join/subquery INTERNAL errors in #27716-#27747. The report step also
searches upstream, but always check yourself: new issues appear at ~30 per day.
