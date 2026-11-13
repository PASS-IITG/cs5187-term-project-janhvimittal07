# RUN_REPORT - DuckDB 2.0.0 targeted run - 2026-10-29 18:41:23

Budget 12.0 h; wall time used 0.00 h.

## Version

```
[PASS] source  /home/cse/.gemini/antigravity/scratch/termproject/src/duckdb is at v2.0.0 (d54cbbe3df)
[PASS] wheel   duckdb 2.0.0 source_id d54cbbe3df
[PASS] dev     duckdb 2.0.0 source_id d54cbbe3df
[PASS] asan    /home/cse/.gemini/antigravity/scratch/termproject/src/build-asan/duckdb is v2.0.0-dev1 d54cbbe3df, core functions work
[PASS] driver  driver-asan runs core functions
[PASS] cov     /home/cse/.gemini/antigravity/scratch/termproject/src/build-cov/duckdb is v2.0.0: coverage numbers are valid
[WARN] newer   gh not installed: check https://github.com/duckdb/duckdb/releases yourself
[WARN] memory  3112 MB available (<4 GB: use JOBS=2 for builds, one ASan job at a time)

preflight ok: 0 failing, 2 warnings
```

## Units run

# Run log (started 2026-10-25 21:22:20, budget 12.0 h)

| time | unit | rc | seconds | summary |
|---|---|---|---|---|
| 2026-10-25 21:22:21 | preflight | 0 | 0 | preflight ok: 0 failing, 2 warnings |
| 2026-10-25 21:49:36 | preflight | 0 | 0 | preflight ok: 0 failing, 1 warnings |
| 2026-10-25 22:02:08 | trig_s200 | 0 | 752 | done in 752s: 0 unique signatures {} |
| 2026-10-25 22:16:50 | trig_s201 | 0 | 882 | done in 881s: 0 unique signatures {} |
| 2026-10-25 22:36:51 | trig_s202 | 0 | 1202 | done in 1201s: 0 unique signatures {} |
| 2026-10-25 22:58:53 | trig_s203 | 0 | 1322 | done in 1321s: 0 unique signatures {} |
| 2026-10-25 23:21:03 | trig_s204 | 0 | 1330 | done in 1329s: 0 unique signatures {} |
| 2026-10-25 23:45:10 | trig_s205 | -15 | 1447 | ... plain pass done 680s |
| 2026-10-26 00:09:19 | trig_s206 | -15 | 1448 | ... plain pass done 728s |
| 2026-10-26 00:30:25 | trig_s207 | 0 | 1266 | done in 1265s: 0 unique signatures {} |
| 2026-10-26 00:51:47 | trig_s208 | 0 | 1282 | done in 1282s: 0 unique signatures {} |
| 2026-10-26 01:15:54 | trig_s209 | -15 | 1447 | ... plain pass done 786s |
| 2026-10-26 01:39:59 | trig_s210 | -15 | 1445 | ... plain pass done 831s |
| 2026-10-26 02:04:04 | trig_s211 | -15 | 1445 | ... plain pass done 872s |
| 2026-10-26 02:12:09 | win_s200 | -15 | 485 | [verify 563] crash      worker died rc=-11  [NOT reproducible alone] |
| 2026-10-26 02:20:17 | win_s201 | -15 | 488 | [verify None] crash      worker died rc=-11  [no case to replay] |
| 2026-10-26 02:28:13 | win_s202 | 0 | 476 | done in 472s: 3 unique signatures |
| 2026-10-26 02:37:59 | join_s200 | 0 | 586 | done in 585s: 0 unique signatures |
| 2026-10-26 02:47:53 | join_s201 | 0 | 594 | done in 593s: 0 unique signatures |
| 2026-10-26 02:57:45 | join_s202 | 0 | 592 | done in 591s: 0 unique signatures |
| 2026-10-26 03:07:38 | join_s203 | 0 | 593 | done in 593s: 0 unique signatures |
| 2026-10-26 03:17:38 | join_s204 | 0 | 599 | done in 599s: 0 unique signatures |
| 2026-10-26 03:40:16 | trig_s212 | 0 | 1358 | done in 1358s: 0 unique signatures {} |
| 2026-10-26 03:43:52 | bu_s213 | 0 | 216 | done in 216s: 1 unique signatures |
| 2026-10-26 03:51:57 | win_s214 | -15 | 485 | [verify None] crash      worker died rc=-11  [no case to replay] |
| 2026-10-26 04:02:14 | join_s215 | 0 | 617 | done in 616s: 0 unique signatures |
| 2026-10-26 04:26:23 | trig_s216 | -15 | 1449 | ... plain pass done 815s |
| 2026-10-26 04:30:21 | bu_s217 | 0 | 238 | done in 237s: 1 unique signatures |
| 2026-10-26 04:38:15 | win_s218 | 0 | 474 | done in 473s: 4 unique signatures |
| 2026-10-26 04:47:56 | join_s219 | 0 | 581 | done in 580s: 0 unique signatures |
| 2026-10-26 05:10:49 | trig_s220 | 0 | 1373 | done in 1372s: 0 unique signatures {} |
| 2026-10-26 05:14:18 | bu_s221 | 0 | 209 | done in 208s: 1 unique signatures |
| 2026-10-26 05:22:26 | win_s222 | -15 | 488 | [verify None] crash      worker died rc=-11  [no case to replay] |
| 2026-10-26 05:32:06 | join_s223 | 0 | 580 | done in 579s: 0 unique signatures |
| 2026-10-26 05:56:11 | trig_s224 | -15 | 1445 | ... plain pass done 805s |
| 2026-10-26 06:00:10 | bu_s225 | 0 | 239 | done in 237s: 1 unique signatures |
| 2026-10-26 06:08:22 | win_s226 | -15 | 491 | [verify None] crash      worker died rc=-11  [no case to replay] |
| 2026-10-26 06:17:49 | join_s227 | 0 | 567 | done in 566s: 0 unique signatures |
| 2026-10-26 06:41:56 | trig_s228 | -15 | 1447 | ... plain pass done 752s |
| 2026-10-26 06:45:36 | bu_s229 | 0 | 220 | done in 215s: 1 unique signatures |
| 2026-10-26 06:53:41 | win_s230 | -15 | 485 | [verify None] crash      worker died rc=127 cannot allocate memory for thread-local data: ABORT [no case to replay] |
| 2026-10-26 07:03:53 | join_s231 | 0 | 611 | done in 610s: 0 unique signatures |
| 2026-10-26 07:27:58 | trig_s232 | -15 | 1445 | ... plain pass done 879s |
| 2026-10-26 07:31:31 | bu_s233 | 0 | 213 | done in 213s: 2 unique signatures |
| 2026-10-26 07:39:36 | win_s234 | -15 | 485 | [verify None] crash      worker died rc=1 See also https://duckdb.org/docs/current/guides/performance/how_to_tune_workloads [no case to replay] |
| 2026-10-26 07:49:29 | join_s235 | 0 | 593 | done in 592s: 0 unique signatures |
| 2026-10-29 18:41:19 | preflight | 0 | 0 | preflight ok: 0 failing, 2 warnings |
| 2026-10-29 18:41:19 | report | 0 | 0 | collecting candidates |


## Summary

* hits collected: 11 (11 auto-verifiable, 0 need human review)
* distinct auto-verifiable groups verified: 4
* **CANDIDATE NEW (reproduces alone, not fixed in the newer snapshot, nothing found upstream): 0**
* **SIBLING CANDIDATES (same error text as a known issue but a different shape - read these first): 0**
* rediscoveries of issues we already know (registry): 0
* reproduces but matches an upstream issue/PR (probably known): 0
* reproduces but could NOT be searched upstream (no error text or stack frames): 0  <- a human must search by hand
* already fixed in the development snapshot: 2
* not reproducible alone / verification error (artifacts): 2

**No candidate new bug.** This is the expected outcome of most runs. Do not report anything as a bug.

## Already fixed in the development snapshot

* `/home/cse/.gemini/antigravity/scratch/termproject/src/results/targeted/candidates/cand000.sql` (asan_added, internal_error, x1): **asan_only** - Binder Error: Cannot concatenate types VARCHAR and VARCHAR[] - an explicit cast is required
  * depends on: nothing special; runs: wheel=ok, wheel_nocte=ok, wheel_threads1=ok, wheel_noopt=ok, wheel_verify=ok, dev=ok, asan=internal_error; searched: nothing
* `/home/cse/.gemini/antigravity/scratch/termproject/src/results/targeted/candidates/cand001.sql` (asan_mutants, internal_error, x1): **asan_only** - Binder Error: Cannot concatenate types VARCHAR and VARCHAR[] - an explicit cast is required
  * depends on: nothing special; runs: wheel=ok, wheel_nocte=ok, wheel_threads1=ok, wheel_noopt=ok, wheel_verify=ok, dev=ok, asan=internal_error; searched: nothing

## Artifacts (did not reproduce alone)

* `/home/cse/.gemini/antigravity/scratch/termproject/src/results/targeted/candidates/cand002.sql` (bu_s233, crash, x1): **not_reproducible** - worker died rc=-11  [NOT reproducible alone]
  * depends on: nothing special; runs: wheel=ok, wheel_nocte=ok, wheel_threads1=ok, wheel_noopt=ok, wheel_verify=ok, dev=ok, asan=ok; searched: nothing
* `/home/cse/.gemini/antigravity/scratch/termproject/src/results/targeted/candidates/cand003.sql` (win_s101, crash, x8): **not_reproducible** - worker died rc=-11  [NOT reproducible alone]
  * depends on: nothing special; runs: wheel=ok, wheel_nocte=ok, wheel_threads1=ok, wheel_noopt=ok, wheel_verify=ok, dev=ok, asan=runtime_error; searched: nothing

## LLM-written generators (numbers come from the engine's metrics.json, not from the agent)

| generator | cases | queries | valid | reach | plan shapes | templates | quality | hits |
|---|---|---|---|---|---|---|---|---|
| aggregate_reuse | 3050 | 7605 | 100% | 75% | 105 | 3407 | ok | 6 |
| delim_cte_subqueries | 3000 | 22376 | 95% | 88% | 6622 | 6082 | ok | 0 |
| distinct_aggregate_rewrite | 3050 | 27520 | 100% | 90% | 1991 | 14556 | ok | 305 |
| example_grouping_sets | 100 | 716 | 90% | 96% | 321 | 386 | ok | 0 |
| feature_interactions | 3050 | 7605 | 100% | 100% | 137 | 168 | ok | 23 |
| grouping_sets | 3000 | 20889 | 88% | 96% | 3250 | 4844 | ok | 0 |
| late_materialization | 3050 | 7610 | 100% | 49% | 189 | 288 | ok | 2 |
| outer_join_simplification | 3050 | 4591 | 100% | 82% | 227 | 256 | ok | 6 |
| partial_aggregate_pushdown | 3050 | 7620 | 100% | 58% | 152 | 6064 | ok | 0 |
| projection_placement | 3050 | 4570 | 100% | 54% | 139 | 839 | ok | 129 |
| projection_pullup | 3050 | 3050 | 100% | 59% | 176 | 1058 | ok | 6 |
| redundant_order_keys | 3050 | 4555 | 100% | 80% | 92 | 779 | ok | 0 |
| window_rewriter | 3050 | 4599 | 100% | 90% | 96 | 288 | ok | 0 |

## Honesty notes

* Nothing here was filed or pushed. Every 'candidate' still needs a human to read the SQL and search upstream.
* Counts are real counts from this run; do not round up in the paper.
