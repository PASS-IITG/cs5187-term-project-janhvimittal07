# Feedback for generator `grouping_sets` (engine-measured, seed 42, DuckDB 2.0.0)

**Quality: ok**
* distinct query templates: 4844; queries per case: {4: 433, 5: 452, 6: 434, 7: 413, 8: 427, 9: 424, 10: 417}
* cases generated 3000, ran 3000, setup valid 3000
* queries 20889, valid 18358 (**valid rate 87.9%**)
* **reach rate 95.5%** of valid queries: the plan changes when the target switch ({'optimizer': 'grouping_sets'}) is flipped; 3250 distinct operator trees
* findings: 0 unique 

## Most common SQL errors (make the generator avoid or deliberately keep them)
* x619: BinderException: Binder Error: column "d" must appear in the GROUP BY clause or must be part of an aggregate f
* x618: BinderException: Binder Error: column "c" must appear in the GROUP BY clause or must be part of an aggregate f
* x582: BinderException: Binder Error: column "b" must appear in the GROUP BY clause or must be part of an aggregate f
* x523: BinderException: Binder Error: column "a" must appear in the GROUP BY clause or must be part of an aggregate f
* x9: BinderException: Binder Error: GROUPING statement cannot be used without groups LINE N: SELECT a, grouping(a),
* x4: BinderException: Binder Error: GROUPING statement cannot be used without groups LINE N: SELECT a, grouping(a) 

## Queries that REACHED the target (keep producing shapes like these)
```sql
SELECT count(*), sum(x) FROM (SELECT a, count(*) AS x FROM t GROUP BY ROLLUP (a)) s
```
```sql
SELECT b, d, c, min(e), max(a), avg(e) FROM t WHERE a > 2 GROUP BY GROUPING SETS ((b, c, d), ()) HAVING count(*) > 1
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT count(*), sum(x) FROM (SELECT b, a, c, count(*) AS x FROM t GROUP BY GROUPING SETS ((a, b, c))) s
```
```sql
SELECT b, min(e), sum(e), max(a) FROM t GROUP BY GROUPING SETS ((b)) HAVING count(*) > 1
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
