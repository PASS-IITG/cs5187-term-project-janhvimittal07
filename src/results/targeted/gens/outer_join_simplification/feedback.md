# Feedback for generator `outer_join_simplification` (engine-measured, seed 42, DuckDB 2.0.0)

**Quality: ok**
* (warning) 100% of queries are valid: a real generator hits some binder errors on unusual shapes
* distinct query templates: 256; queries per case: {1: 1509, 2: 1541}
* cases generated 3050, ran 3050, setup valid 3050
* queries 4591, valid 4591 (**valid rate 100.0%**)
* **reach rate 82.0%** of valid queries: the plan changes when the target switch ({'optimizer': 'outer_join_simplification'}) is flipped; 227 distinct operator trees
* findings: 6 unique {'config_diff': 6}

## Queries that REACHED the target (keep producing shapes like these)
```sql
SELECT count(DISTINCT a1), min(c2) FROM (SELECT t3.a AS a1, t3.b AS b1, t1.a AS a2, t1.c AS c2 FROM t3 FULL OUTER JOIN t1 ON t3.a = t1.a AND t3.b = t1.b WHERE t1.a <= 1) s
```
```sql
SELECT s.*, row_number() OVER (ORDER BY 1) FROM (SELECT t1.a AS a1, t1.b AS b1, t2.a AS a2, t2.c AS c2 FROM t1 FULL OUTER JOIN t2 ON t1.a = t2.a WHERE t2.a <> 5) s ORDER BY ALL
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT * FROM (SELECT t2.a AS a1, t2.b AS b1, t3.a AS a2, t3.c AS c2 FROM t2 FULL OUTER JOIN t3 ON t2.a = t3.a AND t2.b = t3.b WHERE t3.a >= 4) s WHERE 1=1 LIMIT 9 OFFSET 1
```
```sql
SELECT a1, sum(b1) FROM (SELECT t4.a AS a1, t4.b AS b1, t1.a AS a2, t1.c AS c2 FROM t4 FULL OUTER JOIN t1 ON t4.a = t1.a WHERE t1.a >= 5) s GROUP BY a1 ORDER BY ALL
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
