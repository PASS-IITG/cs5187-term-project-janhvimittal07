# DRAFT - NOT FILED - found 2026-10-26 by the LLM generator arm (delim_lateral_chain); checks done 2026-10-26

Title: INTERNAL Error "Failed to bind column reference" for a CROSS JOIN LATERAL with a nested LATERAL count(*) joined to another table (regression vs v1.5.6; filter_pushdown)

### What happens?
A query with `CROSS JOIN LATERAL (... , LATERAL (SELECT count(*) ... FROM range(...)) ...)` whose result is joined to another table with a
condition on the lateral output and an outer column fails at planning time on v2.0.0 with DEFAULT settings:

INTERNAL Error: Failed to bind column reference "" [1.2] (bindings: {#[16.0], #[16.1], #[16.2]})

v1.5.6 runs it. Disabling only `filter_pushdown` makes it pass; `SET delim_join_as_cte=false` also makes it pass.
Deterministic (3/3 runs).

### To Reproduce (full script: results/targeted/candidates/llm_lateral_filterpushdown_default.sql)
```sql
CREATE TABLE t1(a INTEGER, b INTEGER, c VARCHAR);
INSERT INTO t1 VALUES (7,3,''),(2,3,'a'),(1,-1,'c'),(1,0,'b'),(NULL,2,'a'),(3,NULL,'a'),(1,1,'c'),(5,2,'c');
CREATE TABLE t2(a INTEGER, b INTEGER, c VARCHAR);
INSERT INTO t2 VALUES (1,5,'c'),(-1,1,'a'),(3,1,'');
CREATE TABLE t3(a INTEGER, b INTEGER, c VARCHAR);
INSERT INTO t3 VALUES (-1,7,'b'),(2,1,'c'),(1,1,'b');
SELECT o.a, o.b, l0.x
FROM t2 o
CROSS JOIN LATERAL (
    SELECT i0.a AS x, i0.b AS y
    FROM t1 i0, LATERAL (SELECT count(*) AS x, sum(x) AS y FROM (SELECT r.range + o.b AS x, o.c AS y FROM range(greatest(0, least(coalesce(o.a, 0), 4))::BIGINT) r) rr) n0
    WHERE i0.c = o.c
) l0
JOIN t3 i1 ON i1.c = l0.y::VARCHAR AND i1.a <> o.a;
```

### Versions: v1.5.6 wheel: runs | v2.0.0 wheel (d54cbbe3df): INTERNAL Error | 2.0.1.dev2610211350 nightly wheel: INTERNAL Error |
v2.0.0 assertions+ASan build: INTERNAL Error | main 48cca2f7a3 assertions+ASan: INTERNAL Error. With filter_pushdown disabled: runs.

### Needed pieces (reduction): nested LATERAL count(*) with range() (n0) is required; the IN subquery, the EXCEPT ALL lateral and the
LEFT JOIN LATERAL window of the original 5-lateral query are not. Reduced by hand and verified by running, not by reading text.

### Checks (2026-10-26)
* 4,027 issues/PRs updated since 2026-08-01 scanned by text: none combines LATERAL + "Failed to bind column reference" + range()/count(*),
  and none combines filter_pushdown + LATERAL + INTERNAL. Closest: #27452 (SEMI JOIN ON true, join order), #27226 (FULL JOIN NOT IN), #24796
  (count(*) type mismatch, fixed in 1.5.5 and not reproducing on 2.0.0). No commit on origin/main since 2026-10-23 touches filter_pushdown.
* Risk: text search only; re-run the day you file. Not tested: v1.5.5 and older, other platforms.
* AI use: found with an LLM-written generator; reduced and verified with Claude's help.
