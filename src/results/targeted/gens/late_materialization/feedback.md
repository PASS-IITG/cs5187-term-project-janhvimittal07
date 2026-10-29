# Feedback for generator `late_materialization` (engine-measured, seed 42, DuckDB 2.0.0)

**Quality: ok**
* (warning) 100% of queries are valid: a real generator hits some binder errors on unusual shapes
* distinct query templates: 288; queries per case: {2: 1540, 3: 1510}
* cases generated 3050, ran 3050, setup valid 3050
* queries 7610, valid 7610 (**valid rate 100.0%**)
* **reach rate 49.3%** of valid queries: the plan changes when the target switch ({'optimizer': 'late_materialization'}) is flipped; 189 distinct operator trees
* findings: 2 unique {'config_diff': 2}

## Queries that REACHED the target (keep producing shapes like these)
```sql
SELECT * FROM (SELECT payload, id FROM wide_tbl WHERE id >= 20 ORDER BY grp ASC LIMIT 9) s1 UNION ALL SELECT * FROM (SELECT payload, id FROM wide_tbl WHERE id >= 20 ORDER BY grp ASC LIMIT 9) s2 ORDER BY ALL
```
```sql
SELECT * FROM (SELECT id, payload, note FROM wide_tbl WHERE grp < 13 ORDER BY grp ASC LIMIT 22) s1 UNION ALL SELECT * FROM (SELECT id, payload, note FROM wide_tbl WHERE grp < 13 ORDER BY grp ASC LIMIT 22) s2 ORDER BY ALL
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT count(*), max(1) FROM (SELECT payload FROM wide_tbl WHERE grp <= 20 ORDER BY v ASC LIMIT 7) sub
```
```sql
SELECT count(*), max(1) FROM (SELECT w.id, w.payload, r.label FROM wide_tbl w LEFT JOIN ref_tbl r USING (id) WHERE w.id < 25 ORDER BY w.grp DESC LIMIT 22) sub
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
