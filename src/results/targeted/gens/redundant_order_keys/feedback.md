# Feedback for generator `redundant_order_keys` (engine-measured, seed 42, DuckDB 2.0.0)

**Quality: ok**
* (warning) 100% of queries are valid: a real generator hits some binder errors on unusual shapes
* (warning) most reaching queries change only expressions, not operators (fine for expression-level optimizers, check the examples)
* distinct query templates: 779; queries per case: {1: 1545, 2: 1505}
* cases generated 3050, ran 3050, setup valid 3050
* queries 4555, valid 4555 (**valid rate 100.0%**)
* **reach rate 80.1%** of valid queries: the plan changes when the target switch ({'optimizer': 'redundant_order_keys'}) is flipped; 92 distinct operator trees
* findings: 0 unique 

## Queries that REACHED the target (keep producing shapes like these)
```sql
SELECT s.*, rank() OVER (ORDER BY 1) FROM (SELECT id, payload FROM t ORDER BY id ASC, extra ASC) s
```
```sql
SELECT count(*), max(1) FROM (SELECT p, s, val FROM comp ORDER BY p DESC, s DESC, val DESC) sub
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
WITH q AS (SELECT t.id, t.payload, c.val FROM t JOIN comp c ON t.id = c.p ORDER BY t.id, t.payload ASC) SELECT q.*, t2.score FROM q LEFT JOIN t2 ON 1=1 WHERE t2.id = 1
```
```sql
SELECT DISTINCT * FROM (SELECT t.id, t.payload, c.val FROM t JOIN comp c ON t.id = c.p ORDER BY t.id, t.payload ASC) s
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
