# Feedback for generator `partial_aggregate_pushdown` (engine-measured, seed 42, DuckDB 2.0.0)

**Quality: ok**
* (warning) 100% of queries are valid: a real generator hits some binder errors on unusual shapes
* distinct query templates: 6064; queries per case: {2: 1530, 3: 1520}
* cases generated 3050, ran 3050, setup valid 3050
* queries 7620, valid 7620 (**valid rate 100.0%**)
* **reach rate 57.9%** of valid queries: the plan changes when the target switch ({'optimizer': 'partial_aggregate_pushdown'}) is flipped; 152 distinct operator trees
* findings: 0 unique 

## Queries that REACHED the target (keep producing shapes like these)
```sql
SELECT * FROM (SELECT d.grp, sum(f.v), avg(f2.v) FROM fact f JOIN dim d ON f.k = d.k JOIN fact2 f2 ON f.k = f2.k WHERE f.v > 13 GROUP BY d.grp) s WHERE 1=1 ORDER BY ALL
```
```sql
SELECT * FROM (SELECT d.grp, avg(f.v), min(f2.v) FROM fact f LEFT JOIN dim d ON f.k = d.k JOIN fact2 f2 ON f.k = f2.k WHERE f.v > 26 GROUP BY d.grp HAVING max(f2.v) > 82) s1 UNION ALL SELECT * FROM (SELECT d.grp, avg(f.v), min(f2.v) FROM fact f LEFT JOIN dim d ON f.k = d.k JOIN fact2 f2 ON f.k = f2.
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT 1, count(*) FROM (SELECT d.k, min(f.v), sum(f2.v) FROM fact f LEFT JOIN dim d ON f.k = d.k LEFT JOIN fact2 f2 ON f.k = f2.k WHERE d.grp = 1 GROUP BY d.k HAVING sum(f.v) > 82) s GROUP BY 1 ORDER BY ALL
```
```sql
WITH cte AS (SELECT d.k, sum(f.v), count(*) FROM fact f JOIN dim d ON f.k = d.k LEFT JOIN fact2 f2 ON f.k = f2.k WHERE f.v BETWEEN 10 AND 76 GROUP BY d.k) SELECT * FROM cte WHERE 1=1 ORDER BY ALL LIMIT 42
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
