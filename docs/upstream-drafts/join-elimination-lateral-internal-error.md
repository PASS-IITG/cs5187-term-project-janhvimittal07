# DO NOT FILE AS A NEW ISSUE: this is a DUPLICATE of duckdb/duckdb#27445 (checked 2026-10-25, see "Background checks" at the end).
# Kept only as the source for an optional comment on #27445. Original draft title (suggested):
# INTERNAL Error "Attempted to access index 0 within vector of size 0" from join_elimination on a LEFT JOIN LATERAL over an aggregate with a nested LATERAL

Status of this draft: found by the LLM-generator arm (`delim_lateral_chain`) on 2026-10-25, reduced by hand.
SUPERSEDED: the first text searches found nothing, but the full scan below found #27445 (same assertion), and PR #27642 fixes it.
#27449 (SELECT DISTINCT + stale projection map) and #27653 (POSITIONAL JOIN) are different bugs.

### What happens?

A `LEFT JOIN LATERAL` over an aggregate subquery that itself contains a `LATERAL` aggregate fails during planning on the
released v2.0.0:

```
INTERNAL Error: Attempted to access index 0 within vector of size 0
```

On assertion builds (v2.0.0 `d54cbbe3df` and main `48cca2f7a3`) the same query fails earlier with:

```
INTERNAL Error: Assertion triggered in file "src/optimizer/join_elimination.cpp" on line 305: !column_bindings.empty()
```
(line 314 on main).

The failure disappears with `SET disabled_optimizers='join_elimination'` and with `SET delim_join_as_cte=false`. It needs
about 500 or more rows in `t2` (with 100 rows it does not fail), so it depends on statistics.

### To Reproduce

```sql
CREATE TABLE t2(a INTEGER, b INTEGER);
INSERT INTO t2 SELECT i % 29, CASE WHEN i % 5 = 0 THEN NULL ELSE i % 7 END FROM range(500) r(i);
CREATE TABLE t3(a INTEGER, b INTEGER);
SELECT o.a, l0.y
FROM t2 o
LEFT JOIN LATERAL (
    SELECT sum(i0.b) AS y, min(i0.a) AS x
    FROM t2 i0, LATERAL (SELECT count(*) AS y FROM t3 i10 WHERE i10.a = o.a) n0
    WHERE i0.a = o.a
) l0 ON l0.x IS NOT DISTINCT FROM o.b;
```

### Observed

| build | result |
|---|---|
| v2.0.0 release (python wheel, source `d54cbbe3df`) | INTERNAL Error: Attempted to access index 0 within vector of size 0 |
| v2.0.0 with assertions + ASan (`d54cbbe3df`) | Assertion `!column_bindings.empty()` at join_elimination.cpp:305 |
| main `48cca2f7a3` (v2.1.0-dev90060, assertions) | same assertion at join_elimination.cpp:314 |
| any of the above with `disabled_optimizers='join_elimination'` | query runs |
| any of the above with `delim_join_as_cte=false` | query runs |

(v1.5.6: the query runs fine, so this is a regression in 2.0.)

### Notes for the reporter (delete before filing)
* Reduced by hand from a 2,000-row generated script; the `OR i10.b IS NULL` in the original filter is not needed.
* Not checked: whether the result is correct when the optimizer is disabled (the query ran, the output was not compared).
* Environment: Linux (WSL2), Python wheel `duckdb==2.0.0`, ASan build from tag v2.0.0.
* AI use: found with an LLM-written generator, reduced with Claude's help; the reproducer was executed, not generated text.

## Background checks (2026-10-25) - result: duplicate of #27445, fixed by open PR #27642
* Full-text scan of 1,975 issues and PRs updated since 2026-10-05 found #27445 (open, "reproduced", 2026-10-19): same assertion
  `!column_bindings.empty()` at join_elimination.cpp:305 (2.0) / :314 (main), different SQL.
* #27445's own reproducer fails on the released v2.0.0 wheel with the SAME message as ours
  ("INTERNAL Error: Attempted to access index 0 within vector of size 0"), so the release symptom is shared.
* Open PR #27642 "Do not prove uniqueness for a join without conditions" (fixes #27445): a correlated aggregate gets an
  empty-input fallback LEFT join with no conditions, and `ContainDistinctGroup` is called with an empty binding list.
  We applied its diff to main `48cca2f7a3`, rebuilt, and ran: #27445's reproducer, our reduced reproducer, our 2000-row
  script and the original generator script all run without error. So it is the same bug and the PR fixes it.
* v1.5.6 runs all of these fine: a regression in 2.0.
* What our case adds, if the user wants to comment on #27445: (1) it reproduces on the RELEASE wheel with default settings (the
  issue was validated on relassert builds only), (2) a second trigger with no DISTINCT (`LEFT JOIN LATERAL` over an
  aggregate with a nested `LATERAL count(*)`), (3) confirmation that PR #27642 fixes it. This is a comment, not a new issue.
