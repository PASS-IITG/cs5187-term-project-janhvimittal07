# Feedback for generator `projection_placement` (engine-measured, seed 1, DuckDB 2.0.0)

**Quality: ok**
* (warning) 100% of queries are valid: a real generator hits some binder errors on unusual shapes
* distinct query templates: 41; queries per case: {1: 18, 2: 12}
* cases generated 30, ran 30, setup valid 30
* queries 42, valid 42 (**valid rate 100.0%**)
* **reach rate 57.1%** of valid queries: the plan changes when the target switch ({'optimizer': 'projection_placement'}) is flipped; 35 distinct operator trees
* findings: 2 unique {'config_diff': 2}

## Queries that REACHED the target (keep producing shapes like these)
```sql
SELECT s, f_val, count(*) FROM (SELECT d.s, f.f_val, row_number() OVER (PARTITION BY d.i ORDER BY f.f_val) AS rn FROM (SELECT i, substring(v, -3, 14) AS s FROM placement_dim) d JOIN placement_fact f USING (i) WHERE f.f_val > 4) sub GROUP BY s, f_val HAVING count(*) > 1 ORDER BY ALL
```
```sql
WITH q AS (SELECT d.s, f.f_val, f.id FROM (SELECT i, substring(v, -4, 14) AS s FROM placement_dim) d INNER JOIN placement_fact f USING (i) WHERE f.f_val > 4) SELECT * FROM q WHERE 1=1 ORDER BY ALL
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT 1 FROM (SELECT d.s, f.f_val, d2.tag FROM (SELECT i, substring(v, -7, 16) AS s FROM placement_dim) d INNER JOIN placement_fact f USING (i) JOIN placement_dim2 d2 USING (i) WHERE f.f_val > 1) s QUALIFY row_number() OVER () > 0
```
```sql
SELECT s, f_val, count(*) FROM (SELECT d.s, f.f_val FROM (SELECT i, substring(v, -4, 18) AS s FROM placement_dim) d INNER JOIN placement_fact f USING (i) WHERE EXISTS (SELECT 1 FROM placement_dim2 d2 WHERE d2.i = d.i)) sub GROUP BY s, f_val HAVING count(*) > 1 ORDER BY ALL
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
