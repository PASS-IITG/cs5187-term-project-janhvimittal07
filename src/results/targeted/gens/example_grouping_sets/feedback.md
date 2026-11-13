# Feedback for generator `example_grouping_sets` (engine-measured, seed 1, DuckDB 2.0.0)

**Quality: ok**
* distinct query templates: 386; queries per case: {4: 12, 5: 13, 6: 15, 7: 10, 8: 21, 9: 15, 10: 14}
* cases generated 100, ran 100, setup valid 100
* queries 716, valid 643 (**valid rate 89.8%**)
* **reach rate 96.0%** of valid queries: the plan changes when the target switch ({'optimizer': 'grouping_sets'}) is flipped; 321 distinct operator trees
* findings: 0 unique 

## Most common SQL errors (make the generator avoid or deliberately keep them)
* x21: BinderException: Binder Error: column "d" must appear in the GROUP BY clause or must be part of an aggregate f
* x18: BinderException: Binder Error: column "b" must appear in the GROUP BY clause or must be part of an aggregate f
* x14: BinderException: Binder Error: column "c" must appear in the GROUP BY clause or must be part of an aggregate f
* x12: BinderException: Binder Error: column "a" must appear in the GROUP BY clause or must be part of an aggregate f
* x1: BinderException: Binder Error: GROUPING child ""a"" must be a grouping column LINE N: SELECT d, a, grouping(a)
* x1: BinderException: Binder Error: GROUPING child ""a"" must be a grouping column LINE N: SELECT d, c, a, grouping

## Queries that REACHED the target (keep producing shapes like these)
```sql
SELECT c, d, b, a, max(a), grouping(a), count(DISTINCT b) FROM t GROUP BY ROLLUP (c, d, b, a)
```
```sql
SELECT d, count(DISTINCT b) FROM t GROUP BY GROUPING SETS ((d), (d))
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT d, b, sum(e) FROM t GROUP BY GROUPING SETS ((b, d))
```
```sql
SELECT count(*), sum(x) FROM (SELECT a, d, c, b, count(*) AS x FROM t GROUP BY GROUPING SETS ((a, d, b, c))) s
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
