# Feedback for generator `feature_interactions` (engine-measured, seed 42, DuckDB 2.0.0)

**Quality: ok**
* (warning) 100% of queries are valid: a real generator hits some binder errors on unusual shapes
* distinct query templates: 168; queries per case: {2: 1545, 3: 1505}
* cases generated 3050, ran 3050, setup valid 3050
* queries 7605, valid 7605 (**valid rate 100.0%**)
* **reach rate 99.8%** of valid queries: the plan changes when the target switch ({'setting': 'delim_join_as_cte'}) is flipped; 137 distinct operator trees
* findings: 23 unique {'config_diff': 23}

## Queries that REACHED the target (keep producing shapes like these)
```sql
SELECT sum(x) FROM (SELECT count(*) AS x FROM (SELECT id, k, v, tag FROM t1 WHERE EXISTS (SELECT 1 FROM t2 WHERE t2.k = t1.k AND t2.v > 2)) s) sub
```
```sql
SELECT * FROM (SELECT id, k, v, tag FROM t1 WHERE EXISTS (SELECT 1 FROM t2 WHERE t2.k = t1.k AND t2.v > 6)) s WHERE 1=1 ORDER BY ALL LIMIT 13
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT t1.id, t1.k, a.action FROM t1 JOIN audit_log a ON t1.id = a.id WHERE t1.v > (SELECT max(v) FROM t2 WHERE t2.k = t1.k) ORDER BY ALL
```
```sql
SELECT t1.id, t1.k, a.action FROM t1 JOIN audit_log a ON t1.id = a.id WHERE t1.v > (SELECT avg(v) FROM t2 WHERE t2.k = t1.k) ORDER BY ALL
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
