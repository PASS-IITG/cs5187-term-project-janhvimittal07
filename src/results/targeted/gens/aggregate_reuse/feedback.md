# Feedback for generator `aggregate_reuse` (engine-measured, seed 42, DuckDB 2.0.0)

**Quality: ok**
* (warning) 100% of queries are valid: a real generator hits some binder errors on unusual shapes
* distinct query templates: 3407; queries per case: {2: 1545, 3: 1505}
* cases generated 3050, ran 3050, setup valid 3050
* queries 7605, valid 7605 (**valid rate 100.0%**)
* **reach rate 74.9%** of valid queries: the plan changes when the target switch ({'optimizer': 'aggregate_reuse'}) is flipped; 105 distinct operator trees
* findings: 6 unique {'config_diff': 6}

## Queries that REACHED the target (keep producing shapes like these)
```sql
SELECT 1, count(*) FROM (SELECT d.label, o.priority, avg(f.v) FROM reuse_owner o JOIN reuse_dim d ON o.dim_id = d.id AND d.code > 1 JOIN reuse_fact f USING (k) WHERE o.k IN (SELECT k FROM reuse_fact GROUP BY k HAVING avg(v) IS NOT NULL) GROUP BY d.label, o.priority) s GROUP BY 1 ORDER BY ALL
```
```sql
WITH q AS (SELECT d.label, avg(f.w) FROM reuse_owner o JOIN reuse_dim d ON o.dim_id = d.id JOIN reuse_fact f USING (k) WHERE o.k IN (SELECT k FROM reuse_fact GROUP BY k HAVING avg(w) > 12) GROUP BY d.label) SELECT q.*, d.code FROM q JOIN reuse_dim d ON 1=1 WHERE d.id = 10 ORDER BY ALL
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT * FROM (SELECT d.label, sum(f.v) FROM reuse_owner o LEFT JOIN reuse_dim d ON o.dim_id = d.id JOIN reuse_fact f USING (k) WHERE o.k IN (SELECT k FROM reuse_fact GROUP BY k HAVING sum(v) != 28) GROUP BY d.label) s1 UNION ALL SELECT * FROM (SELECT d.label, sum(f.v) FROM reuse_owner o LEFT JOIN r
```
```sql
SELECT o.k, d.id, d.code, sum(f.x) FROM reuse_owner o LEFT JOIN reuse_dim d ON o.dim_id = d.id JOIN reuse_fact f USING (k) WHERE o.k IN (SELECT k FROM reuse_fact GROUP BY k HAVING sum(x) IS NOT NULL) GROUP BY o.k, d.id, d.code
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
