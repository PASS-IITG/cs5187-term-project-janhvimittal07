# Feedback for generator `distinct_aggregate_rewrite` (engine-measured, seed 42, DuckDB 2.0.0)

**Quality: ok**
* (warning) 100% of queries are valid: a real generator hits some binder errors on unusual shapes
* distinct query templates: 14556; queries per case: {6: 433, 7: 437, 8: 407, 9: 447, 10: 437, 11: 454, 12: 435}
* cases generated 3050, ran 3050, setup valid 3050
* queries 27520, valid 27520 (**valid rate 100.0%**)
* **reach rate 90.4%** of valid queries: the plan changes when the target switch ({'optimizer': 'distinct_aggregate_rewrite'}) is flipped; 1991 distinct operator trees
* findings: 305 unique {'config_diff': 305}

## Queries that REACHED the target (keep producing shapes like these)
```sql
SELECT b, a, count(DISTINCT b) FROM t GROUP BY b, a HAVING count(*) > 1 ORDER BY ALL
```
```sql
SELECT * FROM (SELECT sum(DISTINCT d), max(DISTINCT b), sum(DISTINCT e), min(a) FROM t) s WHERE 1=1 ORDER BY ALL LIMIT 16
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT a, c, max(DISTINCT b) FROM t GROUP BY a, c ORDER BY ALL
```
```sql
SELECT count(*), max(m) FROM (SELECT c, d, max(DISTINCT b), count(*) AS m FROM t GROUP BY c, d) s
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
