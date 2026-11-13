# Feedback for generator `window_rewriter` (engine-measured, seed 42, DuckDB 2.0.0)

**Quality: ok**
* (warning) 100% of queries are valid: a real generator hits some binder errors on unusual shapes
* distinct query templates: 288; queries per case: {1: 1501, 2: 1549}
* cases generated 3050, ran 3050, setup valid 3050
* queries 4599, valid 4599 (**valid rate 100.0%**)
* **reach rate 90.3%** of valid queries: the plan changes when the target switch ({'optimizer': 'window_rewriter'}) is flipped; 96 distinct operator trees
* findings: 0 unique 

## Queries that REACHED the target (keep producing shapes like these)
```sql
WITH cte AS (SELECT row_number() OVER () AS rn, a, b, c, d FROM t2) SELECT w.rn, w.d || '_tag' FROM cte w WHERE w.c < 7 ORDER BY ALL
```
```sql
SELECT * FROM (SELECT w.rn, w.a + w.b AS sum_ab FROM (SELECT row_number() OVER () AS rn, a, b, c, d FROM t3) w LIMIT 10) s1 UNION ALL SELECT * FROM (SELECT w.rn, w.a + w.b AS sum_ab FROM (SELECT row_number() OVER () AS rn, a, b, c, d FROM t3) w LIMIT 10) s2 ORDER BY ALL
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT w.c, count(*), sum(w.rn) FROM (SELECT row_number() OVER () AS rn, a, b, c, d FROM t2) w WHERE w.c IN (4, 5, 6) GROUP BY w.c HAVING count(*) > 0 ORDER BY ALL
```
```sql
SELECT sum(x) FROM (SELECT count(*) AS x FROM (SELECT row_number() OVER () AS rn, a, b, c, d FROM t2) w WHERE w.a = 4) sub
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
