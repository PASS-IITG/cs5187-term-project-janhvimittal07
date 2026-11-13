# Feedback for generator `delim_cte_subqueries` (engine-measured, seed 42, DuckDB 2.0.0)

**Quality: ok**
* distinct query templates: 6082; queries per case: {5: 542, 6: 496, 7: 475, 8: 509, 9: 487, 10: 491}
* cases generated 3000, ran 3000, setup valid 3000
* queries 22376, valid 21239 (**valid rate 94.9%**)
* **reach rate 88.1%** of valid queries: the plan changes when the target switch ({'setting': 'delim_join_as_cte'}) is flipped; 6622 distinct operator trees
* findings: 0 unique 

## Most common SQL errors (make the generator avoid or deliberately keep them)
* x639: BinderException: Binder Error: column "b" must appear in the GROUP BY clause or must be part of an aggregate f
* x435: BinderException: Binder Error: column "c" must appear in the GROUP BY clause or must be part of an aggregate f
* x21: BinderException: Binder Error: Referenced table "o" not found! Candidate tables: "x" LINE N: ... iN.b <= COALE
* x6: BinderException: Binder Error: Referenced table "o" not found! Candidate tables: "x" LINE N: ... count(iN.a) F
* x5: BinderException: Binder Error: Referenced table "o" not found! Candidate tables: "x" LINE N: ... AND iN.b <= C
* x5: BinderException: Binder Error: Referenced table "o" not found! Candidate tables: "x" LINE N: ... BY iN.c ORDER

## Queries that REACHED the target (keep producing shapes like these)
```sql
SELECT o.a, o.b FROM t1 o WHERE NOT EXISTS (SELECT 1 FROM t2 p WHERE p.a < o.b)
```
```sql
SELECT o.a, CASE WHEN COALESCE((SELECT max(g) FROM (SELECT avg(i1.a) AS g FROM t1 i1 WHERE i1.b = o.b GROUP BY i1.c)), 0) > o.a THEN COALESCE((SELECT max(w) FROM (SELECT row_number() OVER (PARTITION BY i1.c ORDER BY i1.a, i1.b) AS w FROM t3 i1 WHERE i1.a = o.a AND i1.c = o.c)), 0) ELSE 2 END FROM t1
```

## Valid queries that did NOT reach the target (change what makes the target code fire)
```sql
SELECT o.a, j.b FROM t1 o FULL JOIN t2 j ON j.b + o.a > o.a AND EXISTS (SELECT 1 FROM t3 q WHERE q.a = j.a)
```
```sql
SELECT o.a, j.b FROM t1 o FULL JOIN t2 j ON j.a < o.b AND EXISTS (SELECT 1 FROM t3 q WHERE q.a = j.a)
```

## Next round
Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless.
