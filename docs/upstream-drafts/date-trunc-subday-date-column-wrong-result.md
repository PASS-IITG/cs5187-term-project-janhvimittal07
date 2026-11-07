**Title:** `date_trunc('hour'/'minute'/'second', <DATE column>)` compared with a TIMESTAMP returns wrong results

### What happens?

When `date_trunc` with a sub-day part (`hour`, `minute`, `second`, ...) is applied to a **DATE column** and compared with a TIMESTAMP constant, the result is wrong. This happens with every comparison operator (`=`, `<>`, `<`, `<=`, `>`, `>=`, `IS [NOT] DISTINCT FROM`), in both `WHERE` and `SELECT`. With `SET disabled_optimizers = 'expression_rewriter'` the result is correct, and so is the same expression on a constant.

The cause seems to be `DateTruncSimplificationRule` (`src/optimizer/rule/date_trunc_simplification.cpp`). It rewrites `date_trunc(part, col) OP c` into bounds on `col`, and `CreateTrunc` / `CreateTruncAdd` cast those bounds to the column type (`AddDefaultCastToType(..., column_part.GetReturnType())`). For a DATE column and a sub-day part, the upper bound `date_trunc('hour', c + INTERVAL 1 hour)` = `2024-03-28 01:00:00` becomes `2024-03-28` after the cast, so `=` turns into `x >= DATE '2024-03-28' AND x < DATE '2024-03-28'`, which is always false (EXPLAIN shows `Empty Result`). The other operators go wrong in the same way, because bounds that are not at midnight get truncated to the date.

`date_trunc('day', ...)` and coarser parts are not affected.

### To Reproduce

```sql
CREATE TABLE t(x DATE);
INSERT INTO t VALUES (DATE '2024-03-27'), (DATE '2024-03-28'), (DATE '2024-03-29');

SELECT count(*) FROM t WHERE date_trunc('hour', x) = TIMESTAMP '2024-03-28 00:00:00';
-- 0, expected 1

SELECT count(*) FROM t WHERE date_trunc('hour', x) > TIMESTAMP '2024-03-28 05:00:00';
-- 2, expected 1

SELECT x, date_trunc('hour', x) = TIMESTAMP '2024-03-28 00:00:00' FROM t ORDER BY x;
-- false for 2024-03-28, expected true

SELECT date_trunc('hour', DATE '2024-03-28') = TIMESTAMP '2024-03-28 00:00:00';
-- true (correct)

SET disabled_optimizers = 'expression_rewriter';
SELECT count(*) FROM t WHERE date_trunc('hour', x) = TIMESTAMP '2024-03-28 00:00:00';
-- 1 (correct)
```

Results on the table above (got / expected):

| expression | got | expected |
|---|---|---|
| `date_trunc('hour', x) = TIMESTAMP '2024-03-28 00:00:00'` | 0 | 1 |
| `date_trunc('hour', x) <> TIMESTAMP '2024-03-28 00:00:00'` | 3 | 2 |
| `date_trunc('hour', x) <= TIMESTAMP '2024-03-28 00:00:00'` | 1 | 2 |
| `date_trunc('hour', x) > TIMESTAMP '2024-03-28 00:00:00'` | 2 | 1 |
| `date_trunc('hour', x) < TIMESTAMP '2024-03-28 05:00:00'` | 1 | 2 |
| `date_trunc('hour', x) >= TIMESTAMP '2024-03-28 05:00:00'` | 2 | 1 |

`minute` and `second` give the same results.

### OS:

Linux x86_64 (WSL2)

### DuckDB Version:

2.0.0 (d54cbbe3df); also reproduced on main nightly builds v2.1.0-alpha51401 (6287a4e595) and v2.1.0-dev91267 (b3a34f5d69)

### DuckDB Client:

Python and CLI

### Hardware:

### Full Name:

Prabhnoor

### Affiliation:

<fill in>

### Did you include all relevant configuration (e.g., CPU architecture, Linux distribution) to reproduce the issue?

Yes

### Did you include all code required to reproduce the issue?

Yes

### Have you tested this issue on the latest nightly build?

Yes
