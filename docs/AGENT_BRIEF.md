# Task brief for an AI coding agent (Gemini / Antigravity / Claude / ...): DuckDB 2.0.0 targeted bug hunt

You are running a long, mostly automated bug hunt against the **released DuckDB v2.0.0** for a university project. The
repository does the work; **your job is to start it, keep it healthy, and report honestly.** Read this whole file first.

## Ground truth you must not forget
* A generic "find bugs" run on 2.0.0 finds nothing new: ~700,000 queries under sanitizers already produced only bugs that
  upstream had reported. Zero verified findings is the *normal* outcome. Reporting zero honestly is a success.
  Reporting a fake or already-known bug is a failure.
* The repo's scripts target code that is **new in 2.0** (triggers, the delim-join-to-CTE rewrite, new optimizers, ...)
  and verify every hit mechanically. Do not replace them with your own ad-hoc fuzzing; your own SQL goes through Phase B, which feeds the same verification.

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

## Phase B - you are also the generator (this is where an LLM adds value; do it while the scripts run)
The scripts fuzz mechanically. You can do what they cannot: **read the new C++ and write SQL aimed at it.** Start Phase B
right after step 3 starts the run (writing SQL is light; the replays obey rule 5: one heavy job at a time).

What to read (everything is on disk; no need to paste code anywhere):
| Input | Where | Use |
|---|---|---|
| Where the new code is | `$FUZZ_WORK/results/hints-v200-new.json` (27k branches/constants inside C++ changed since 1.5.6). Make it with `PY src/static/new_code_hints.py ...` if missing (runbook section 2). Rank files: `python3 -c "import json,collections;h=json.load(open('$FUZZ_WORK/results/hints-v200-new.json'));[print(n,f) for f,n in collections.Counter(x['file'] for x in h).most_common(40)]"` | pick the top ~25 files, read them in `$FUZZ_WORK/duckdb/<file>` |
| Valid 2.0 syntax | `$FUZZ_WORK/results/feature-cards.json` (make with `PY src/llm/feature_cards.py build`) and `$FUZZ_WORK/corpus/seeds-v200-added/*.sql` (the tests added in 2.0) | copy the syntax exactly; never guess syntax of new features |
| New features to target | triggers (`CREATE TRIGGER`, `REFERENCING`, FOR EACH ROW/STATEMENT, MERGE/RETURNING inside bodies), the delim-join-to-CTE rewrite (setting `delim_join_as_cte`; every correlated subquery), the 14 new optimizers (`SELECT name FROM duckdb_optimizers()` minus the 1.5.6 list), `variant_*` / `json_*` functions, aggregate state export, the `tuple` and `timestamptz_ns` types, `NEAREST`/similarity joins, `DEFAULT` binding in INSERT/UPDATE/MERGE, ordered aggregates and quantiles, new settings (`approximate_join_order_threshold`, `regex_match_operator_semantics`, `error_on_division_by_zero`) | combine them with each other (trigger + MERGE + RETURNING, correlated subquery inside window frame inside CTE, ...) |
| Bugs already known | the list at the end of this file; also `gh issue list --repo duckdb/duckdb --limit 100 --json number,title` for your target area before you spend time on it | avoid duplicates |
| Coverage | **not available in this run** (the coverage build is for 1.5.6; rebuilding takes hours). The changed-code hints replace it. | - |

What counts as a finding: an `INTERNAL`/`FATAL` error, a crash, a sanitizer report, or **two configurations disagreeing on
the result** (`delim_join_as_cte`, `disabled_optimizers`, `threads`, `approximate_join_order_threshold=2`,
`PRAGMA enable_verification`). Ordinary errors, odd-looking SQL and "I think this should work" are not findings.

Procedure, per target (aim for at least 25 targets, 20-40 scripts each):
1. Read the C++ around the hints; note the branches, limits and special cases (empty input, NULLs, duplicates, 2048-row
   vector boundaries, huge/negative values, nesting depth, combinations with another new feature).
2. Write self-contained scripts to `$FUZZ_WORK/corpus/llm/agent/<feature>__NN.sql` (create the tables inside each script;
   at most 60 statements; **no** `COPY`, `ATTACH`, `INSTALL`, `LOAD`, `read_*()`, file paths, URLs or extensions).
3. Check them on the wheel (`PY` with `duckdb`); delete any script whose first three statements fail (invented syntax).
4. Replay: when `pgrep -f asan_replay` shows nothing running, from the scratch directory run
   `PY <repo>/src/harness/asan_replay.py --sql-dir $FUZZ_WORK/corpus/llm/agent --out $FUZZ_WORK/results/targeted/asan-agent`
   and `PY <repo>/src/harness/sweep_oracle.py --seeds $FUZZ_WORK/corpus/llm/agent --out $FUZZ_WORK/results/targeted/sweep-agent.jsonl --workers 2`
   (the second compares configurations). For a second batch use `--out .../asan-agent2`.
5. After the last replay run `PY <repo>/src/harness/targeted_run.py --report-only`; your hits are verified and searched
   upstream like the scripts' hits and appear in `RUN_REPORT.md`.
Keep `results/targeted/AGENT_NOTES.md` up to date (targets done, scripts written/kept, replay runs, ideas left) so you can
resume after losing context. Report in `FINAL_SUMMARY.md` how many scripts you wrote and kept, per feature.

## Known upstream bugs (never re-report)
#27446, #27448, #27450, #27631, #27759, and the join/subquery INTERNAL errors in #27716-#27747. The report step also
searches upstream, but always check yourself: new issues appear at ~30 per day.
