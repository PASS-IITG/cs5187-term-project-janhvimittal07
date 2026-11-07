# DRAFT - NOT FILED - for the user to review and file themself (checked 2026-10-25)

Suggested title:
INTERNAL Error "Failed to bind column reference" when two identical EXISTS-over-grouped-subquery blocks are combined with UNION ALL and `delim_join_as_cte=false` (regression vs v1.5.6)

### What happens?

With the (deprecated) setting `delim_join_as_cte = false`, which keeps the pre-2.0 decorrelation, a query that contains the same
`EXISTS (... GROUP BY k, g HAVING ...)` block twice (here via `UNION ALL`) fails at planning time on v2.0.0:

```
INTERNAL Error: Failed to bind column reference "g" [2.1] (bindings: {#[53.0], #[53.1], #[53.2], #[53.3]})
```

The same query runs on v1.5.6, and on v2.0.0 with the default `delim_join_as_cte = true`. Each of the two blocks alone also runs.
Disabling any ONE of `common_subplan`, `unused_columns`, `column_lifetime`, `statistics_propagation` or
`compressed_materialization` makes it pass (`aggregate_reuse` and `join_order` do not matter), so it looks like an interaction
between common-subplan merging of the duplicated block and the column-pruning passes on the non-CTE delim join.
It needs a two-column correlation key (`k`, `g`) and enough rows (with 500 rows it does not fail; 2048 does).

### To Reproduce

```sql
SET delim_join_as_cte = false;
CREATE TABLE o(k INTEGER, g INTEGER);
CREATE TABLE f(k INTEGER, g INTEGER, w INTEGER);
INSERT INTO o SELECT i % 5, i % 3 FROM range(20) r(i);
INSERT INTO f SELECT i % 5, i % 3, i % 9 FROM range(2048) r(i);
SELECT o.k, o.g, min(f.w) FROM f JOIN o ON f.k = o.k
WHERE EXISTS (SELECT 1 FROM (SELECT k, g FROM f GROUP BY k, g HAVING min(w) >= 3) s WHERE s.k = f.k AND s.g = f.g)
GROUP BY o.k, o.g
UNION ALL
SELECT o.k, o.g, min(f.w) FROM f JOIN o ON f.k = o.k
WHERE EXISTS (SELECT 1 FROM (SELECT k, g FROM f GROUP BY k, g HAVING min(w) >= 3) s WHERE s.k = f.k AND s.g = f.g)
GROUP BY o.k, o.g;
```

### Versions tested

| build | `delim_join_as_cte=false` | default |
|---|---|---|
| v1.5.6 (python wheel) | n/a (setting does not exist) | runs |
| v2.0.0 (python wheel, source `d54cbbe3df`) | INTERNAL Error | runs |
| v2.0.1.dev2610211350 (nightly wheel) | INTERNAL Error | runs |
| v2.0.0 assertions + ASan/UBSan build | INTERNAL Error | runs |
| main `48cca2f7a3` (v2.1.0-dev90060, assertions + ASan) | INTERNAL Error | runs |

### Honest scope (delete before filing, or keep as context)
* Only reproduces with the deprecated setting, so user impact is low; it is still an InternalException (instance invalidated).
* The default path (`delim_join_as_cte=true`) is not affected by this query; I did not find a default-settings variant.
* Found by an LLM-written generator that was aimed at `aggregate_reuse` (the generator's target guess was wrong: that optimizer is not
  involved). Reduced and verified by running the script on each build, not by reading text.
* Environment: Linux (WSL2), x86_64.

## Background checks done (2026-10-25)
* Scanned 4,027 issues and PRs updated since 2026-08-01 (titles and bodies): none combines "Failed to bind column reference" with
  common_subplan and a non-CTE delim join. Closest, judged different: #27761 (MERGE source bindings, open PR), #25554 (USING KEY recursion),
  #24627/#24535 (GROUPING() bindings in the ordered list rewrite). Issues that mention `delim_join_as_cte=false` are performance/plan
  ones (#25778, #27675, #27727) or #27759 (ours; fix PR #27839 opened 2026-10-25).
* No commit on origin/main since 2026-10-23 touches common_subplan_optimizer, column_lifetime_analyzer, remove_unused_columns, flatten_dependent_join,
  statistics or compressed_materialization (checked by file path).
* Also searched older issues by text ("common_subplan", "Failed to bind column reference"); the open "Failed to bind column reference" issues
  #27606, #27719, #27226 were read: different shapes, none mentions common_subplan, UNION or delim_join_as_cte. The older common_subplan
  issues (#26427, #25819, #25170, #24624, #23585, #22133) are wrong-result or other internal errors without this message.
* Remaining risk: GitHub search is text-based; a duplicate described in other words could exist. Re-run the checks the day you file.
