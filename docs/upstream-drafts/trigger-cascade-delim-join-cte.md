<!-- FILED 2026-10-23 by Prabhnoor: https://github.com/duckdb/duckdb/issues/27759 -->

**Title:** INTERNAL Error "Conflicting or cyclic column binding replacement" when a `FOR EACH ROW` trigger inserts into a table that has a `BEFORE INSERT` trigger (`delim_join_as_cte`)

### What happens?

A `FOR EACH ROW` trigger whose body inserts into a second table fails with an INTERNAL error as soon as that second
table has a `BEFORE INSERT ... FOR EACH STATEMENT` trigger. The statement is rolled back, nothing is corrupted, and the
database stays usable, but the valid statement cannot be executed.

The error comes from the delim-join-to-CTE rewrite (new in 2.0, setting `delim_join_as_cte`, default `true`). With
`SET delim_join_as_cte = false` the same statements succeed and give the expected result.

### To Reproduce

```sql
CREATE TABLE t(a INT);
CREATE TABLE u(a INT);
CREATE TABLE l(x INT);

CREATE TRIGGER b BEFORE INSERT ON u FOR EACH STATEMENT INSERT INTO l VALUES (1);
CREATE TRIGGER r AFTER  INSERT ON t FOR EACH ROW       INSERT INTO u VALUES (NEW.a);

INSERT INTO t VALUES (1);
-- INTERNAL Error: Conflicting or cyclic column binding replacement for #[54.0]
```

Expected: `t = [1]`, `u = [1]`, `l = [1]` (this is what `SET delim_join_as_cte = false` gives).

### Details

* Fails on the released **v2.0.0** (`d54cbbe3df`, Python wheel and a source build with assertions + ASan/UBSan) and on
  **2.0.1.dev2610211350** (`d9c3e9ae11`). Linux x86_64.
* Also fails with a row-level `AFTER DELETE` trigger (`... FOR EACH ROW INSERT INTO u VALUES (OLD.a)`), and for multi-row
  inserts. The column numbers in the message vary (`#[47.0]`, `#[53.0]`, `#[54.0]`).
* Does **not** fail when: the cascading trigger on `t` is `FOR EACH STATEMENT`; the trigger on `u` is `AFTER` instead of
  `BEFORE`; or `u` is inserted into directly. A generated column on `u` is not needed.
* Still fails with every optimizer disabled (`SET disabled_optimizers = '<all>'`), so it is not an optimizer pass.
* After the error: `t` unchanged, `u` and `l` empty (statement rolled back), later statements work.

Stack (assertions build, symbolised):

```
INTERNAL Error: Conflicting or cyclic column binding replacement for #[54.0]
  ColumnBindingReplacer::Add            src/optimizer/column_binding_replacer.cpp:83
  CreateConstructedBindingReplacements  src/planner/subquery/delim_join_cte_rewriter.cpp
  MaterializeDelimJoinAsCTE             src/planner/subquery/delim_join_cte_rewriter.cpp
  RewriteDelimJoinsToCTEs               src/planner/subquery/delim_join_cte_rewriter.cpp:2831
```

### Environment

* DuckDB v2.0.0 `d54cbbe3df` (also 2.0.1.dev2610211350 `d9c3e9ae11`), Linux x86_64, Python 3.14 wheel / CLI.

Related but different: #27631 (two MERGE trigger bodies, "Failed to bind column reference", goes away with `projection_pullup` disabled). This one fails with every optimizer disabled and only with `delim_join_as_cte = true`.

Found by fuzzing the new trigger feature (random trigger definitions + DML sequences), reduced by hand.
