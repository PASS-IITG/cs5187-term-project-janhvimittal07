# Feedback for generator `projection_placement` (engine-measured, seed 42, DuckDB 2.0.0)

**Quality: ok**
* (warning) 100% of queries are valid: a real generator hits some binder errors on unusual shapes
* distinct query templates: 839; queries per case: {1: 1530, 2: 1520}
* cases generated 3050, ran 3050, setup valid 3050
* queries 4570, valid 4570 (**valid rate 100.0%**)
* **reach rate 53.6%** of valid queries: the plan changes when the target switch ({'optimizer': 'projection_placement'}) is flipped; 139 distinct operator trees
* findings: 129 unique {'config_diff': 129}

## Queries that REACHED the target (keep producing shapes like these)
```sql
SELECT s.*, rank() OVER (ORDER BY f_val) FROM (SELECT d.s, f.f_val, f.id FROM (SELECT i, substring(v, 2, 12) AS s FROM placement_dim) d INNER JOIN placement_fact f USING (i) WHERE f.f_val > 4) s ORDER BY ALL
```
```sql
SELECT DISTINCT * FROM (SELECT d.s, f.f_val, row_number() OVER (PARTITION BY d.i ORDER BY f.f_val) AS rn FROM (SELECT i, substring_grapheme(v, 1, 11) AS s FROM placement_dim) d LEFT JOIN placement_fact f USING (i) WHERE f.f_val > 1) s ORDER BY ALL
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT s, count(*) FROM (SELECT d.s, f.f_val FROM (SELECT i, substring(v, -9, 10) AS s FROM placement_dim) d LEFT JOIN placement_fact f USING (i) WHERE EXISTS (SELECT 1 FROM placement_dim2 d2 WHERE d2.i = d.i)) sub GROUP BY s
```
```sql
SELECT s, count(*) FROM (SELECT f.f_val, d.s FROM placement_fact f LEFT JOIN (SELECT i, substring(v, 1, 13) AS s FROM placement_dim) d USING (i) WHERE f.f_val > 0) sub GROUP BY s
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
