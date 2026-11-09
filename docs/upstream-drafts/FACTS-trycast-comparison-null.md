FACTS ONLY (DuckDB's AI_POLICY: the issue text must be written by the user in their own words)

Found by: hunt1 (automated loop), target comparison_simplification, round 2, 2026-11-08.

Minimal reproducer (verified on 2.0.0 d54cbbe3df and main b3a34f5d69):
  CREATE TABLE t(d DATE);
  INSERT INTO t VALUES (DATE '294247-01-11'), (DATE '2024-01-01');
  SELECT count(*) FROM t WHERE TRY_CAST(d AS TIMESTAMP) <> TIMESTAMP '1970-01-01 12:00:00';
      -> 2 (optimizer on), 1 with SET disabled_optimizers='expression_rewriter' (correct)
  SELECT d::VARCHAR, TRY_CAST(d AS TIMESTAMP) = TIMESTAMP '1970-01-01 12:00:00' FROM t ORDER BY d;
      -> 294247-01-11 gives false (on) vs NULL (off, correct)
  also wrong: WHERE NOT (TRY_CAST(d AS TIMESTAMP) = ...)  -> 2 vs 1;  (...) IS NULL -> 0 vs 1

Why: 294247-01-11 is one day past the largest TIMESTAMP, so TRY_CAST(d AS TIMESTAMP) is NULL and the
comparison is NULL. ComparisonSimplificationRule (src/optimizer/rule/comparison_simplification.cpp,
DateTimestampComparisonIsInvertible, from PR 22678 "Be careful at midnight") folds
"DATE column cast to TIMESTAMP = non-midnight TIMESTAMP" to FALSE "preserving NULL" - it only keeps NULL
when the COLUMN is NULL, it does not account for a TRY_CAST that fails (the rule does not distinguish
TRY_CAST from CAST). In WHERE x = ... the NULL/false difference is invisible; under <>, NOT, IS NULL
and in the SELECT list it changes results.

Upstream search (2026-11-08): no matching issue or PR.
