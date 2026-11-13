# Feedback for generator `projection_pullup` (engine-measured, seed 42, DuckDB 2.0.0)

**Quality: ok**
* (warning) 100% of queries are valid: a real generator hits some binder errors on unusual shapes
* (warning) every case has exactly 1 queries (fine only if the queries themselves vary structurally)
* distinct query templates: 1058; queries per case: {1: 3050}
* cases generated 3050, ran 3050, setup valid 3050
* queries 3050, valid 3050 (**valid rate 100.0%**)
* **reach rate 59.0%** of valid queries: the plan changes when the target switch ({'optimizer': 'projection_pullup'}) is flipped; 176 distinct operator trees
* findings: 6 unique {'config_diff': 6}

## Queries that REACHED the target (keep producing shapes like these)
```sql
WITH q AS (SELECT t2.a, j.b FROM t2 JOIN (SELECT x_838.a AS a, y_252.b AS b FROM t3 x_838 JOIN t2 y_252 ON x_838.a = y_252.a) j ON t2.a = j.a) SELECT * FROM q WHERE 1=1 ORDER BY ALL
```
```sql
SELECT sum(1) FROM (SELECT j.a, j.b, t1.c FROM (SELECT x_941.a AS a, y_722.b AS b FROM t2 x_941 INNER JOIN t3 y_722 ON x_941.a = y_722.a) j JOIN t1 ON j.a = t1.a) s
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT count(*), max(1) FROM (SELECT j1.a, j2.b FROM (SELECT x_668.a AS a, y_915.b AS b FROM t2 x_668 JOIN t2 y_915 ON x_668.a = y_915.a) j1 CROSS JOIN t3 j2 WHERE j1.a < 3 AND j2.a < 3) s
```
```sql
SELECT sum(x) FROM (SELECT count(*) AS x FROM (SELECT j.a, count(*), sum(j.b) AS sb FROM (SELECT x_262.a AS a, y_218.b AS b FROM t4 x_262 LEFT JOIN t3 y_218 ON x_262.a = y_218.a) j GROUP BY j.a) s) sub
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
