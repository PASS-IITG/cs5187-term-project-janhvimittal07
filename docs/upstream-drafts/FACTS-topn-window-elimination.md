UPDATE 2026-11-08: A is ALREADY FIXED on main by merged PR #28405 (6 Nov; verified on a local build of d4eea3f208: returns -2.0/2.0). DO NOT FILE A. B still fails on that build; no PR found.
The 'main' in the text below meant the 3 Nov nightly b3a34f5d69, which is older than that fix.

FACTS ONLY. Found by hunt1, target topn_window_elimination, 2026-11-08. Both verified on 2.0.0 d54cbbe3df and main b3a34f5d69.

A. Nested cast across two subquery levels loses the inner cast (wrong value):
  CREATE TABLE t(id INTEGER, g INTEGER, o BIGINT, d DOUBLE);
  INSERT INTO t VALUES (18, 2, 1005, -1.5), (19, 2, 1006, 2.5);
  SELECT id, v FROM (SELECT id, g, o, CAST(m AS DOUBLE) AS v FROM (SELECT id, g, o, CAST(d AS INTEGER) AS m FROM t))
  QUALIFY row_number() OVER (PARTITION BY g ORDER BY o) <= 3 ORDER BY id;
    -> 18,-1.5 / 19,2.5 (default); 18,-2.0 / 19,2.0 with SET disabled_optimizers='top_n_window_elimination' (correct)
  Same casts in ONE expression, CAST(CAST(d AS INTEGER) AS DOUBLE), are correct, so it needs the two projections.
  Related: issue 24609 / PR 24846 (fixed nested casts in late materialization, IsRecreatableByLateMaterialization); this looks like an
  incomplete fix for casts spread over separate projections (inferred from the behaviour and that PR's text, not traced in a debugger).
  Upstream search: no open issue.

B. Signed zero through a join (minor):
  CREATE TABLE a (g INTEGER, g2 VARCHAR, k BIGINT, c FLOAT, pl INT[]); CREATE TABLE b (c FLOAT, w INTEGER);
  INSERT INTO a VALUES (NULL, 'x1', 2, -0.0::FLOAT, [2]); INSERT INTO b VALUES (0.0::FLOAT, 0);
  SELECT g, atan2(bc, -1.0::FLOAT) AS o_bc, ak, rn FROM (SELECT a.g AS g, b.c AS bc, a.k AS ak,
    row_number() OVER (PARTITION BY a.g, a.g2 ORDER BY a.k ASC NULLS LAST) AS rn FROM b JOIN a ON b.c = a.c) WHERE rn = 1;
    -> o_bc = -3.14159 (default), +3.14159 with the optimizer off (correct: b.c is +0.0)
  A simplified query without g/ak in the outer SELECT did NOT reproduce. Cause not investigated.

Discarded: the r2 candidate (BLOB ORDER BY k with ties) is a tie false positive, row_number may pick either tied row.

UPDATE on B (analysed 2026-11-08, local build of main d4eea3f208):
Cause: src/optimizer/topn_window_elimination.cpp, late-materialization eligibility, COMPARISON_JOIN case (~line 1022): "we allow replacing references to
join columns as they are equal to the other side by condition" (replaceable_bindings). Equal is not identical: 0.0 = -0.0. With late materialization the
output for b.c is read back from table a via a rowid join, so a's -0.0 is returned for b.c.
Affected (observed): FLOAT, DOUBLE, LIST of FLOAT. Not affected in the same query shape: INTERVAL ('1 month' vs '30 days', reason not investigated), VARCHAR COLLATE NOCASE, DECIMAL of different scales.
Trigger needs late materialization: selecting b.c alone or with g did not trigger it; b.c + a.k did. Open PRs 27470 and 26025 do not touch replaceable_bindings.
Minimal:
  CREATE TABLE a(g INT, g2 VARCHAR, k BIGINT, c DOUBLE); CREATE TABLE b(c DOUBLE, w INT);
  INSERT INTO a VALUES (NULL, 'x', 2, -0.0::DOUBLE); INSERT INTO b VALUES (0.0::DOUBLE, 0);
  SELECT bc::VARCHAR, ak FROM (SELECT b.c AS bc, a.k AS ak, row_number() OVER (PARTITION BY a.g, a.g2 ORDER BY a.k) AS rn FROM b JOIN a ON b.c = a.c) WHERE rn = 1;
  -> -0.0,2 (default); 0.0,2 with SET disabled_optimizers='top_n_window_elimination' (correct)
