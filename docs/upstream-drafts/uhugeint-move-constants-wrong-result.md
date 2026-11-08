**Title:** Silent wrong results for `uhugeint_col + c1 = c2` when a constant is above the HUGEINT maximum (IntegralValue::Get wraps UHUGEINT)

### What happens?

A comparison of `x + c1` / `x - c1` on a `UHUGEINT` column with constants larger than `170141183460469231731687303715884105727` (HUGEINT max, 2^127 - 1) returns wrong results, without any error. With `SET disabled_optimizers = 'expression_rewriter'` the results are correct.

`MoveConstantsRule` (`src/optimizer/rule/move_constants.cpp`) reads both constants with `IntegralValue::Get`, which returns a `hugeint_t`. For `UHUGEINT` it does an unchecked conversion:

```cpp
// src/common/types/value.cpp, IntegralValue::Get
case PhysicalType::UINT128:
    return static_cast<hugeint_t>(UhugeIntValue::Get(value));
```

Values above 2^127 - 1 do not fit in `hugeint_t`, so they wrap to negative numbers, the moved constant is wrong, and rows are silently dropped or kept.

This is the `UINT128` sibling of #28535 (the `UINT64` branch of the same function, which raises an INTERNAL error instead). The open fix for #28535, #28540, only changes the `UINT64` case, so it does not fix this one. Widening is not enough here, because `hugeint_t` cannot represent these `UHUGEINT` values; the rewrite probably has to skip constants that do not fit.

### To Reproduce

```sql
CREATE TABLE t(x UHUGEINT);
INSERT INTO t VALUES (170141183460469231731687303715884105728), (5), (340282366920938463463374607431768211454);

SELECT x FROM t WHERE x + 1 = 170141183460469231731687303715884105729;
-- no rows, expected 170141183460469231731687303715884105728

SELECT count(*) FROM t WHERE x + 1 = 340282366920938463463374607431768211455;
-- 0, expected 1

SELECT x, x + 1 = 170141183460469231731687303715884105729 AS b FROM t ORDER BY x;
-- b is false for x = 170141183460469231731687303715884105728, expected true

SET disabled_optimizers = 'expression_rewriter';
SELECT x FROM t WHERE x + 1 = 170141183460469231731687303715884105729;           -- 1 row (correct)
SELECT count(*) FROM t WHERE x + 1 = 340282366920938463463374607431768211455;    -- 1 (correct)
```

### OS:

Linux x86_64 (WSL2)

### DuckDB Version:

2.0.0 (d54cbbe3df); also reproduced on the main nightly build v2.1.0-dev91267 (b3a34f5d69), where the `UINT128` case of `IntegralValue::Get` is unchanged

### DuckDB Client:

Python and CLI

### Hardware:

### Full Name:

Prabhnoor

### Affiliation:

IIT Guwahati

### Did you include all relevant configuration (e.g., CPU architecture, Linux distribution) to reproduce the issue?

Yes

### Did you include all code required to reproduce the issue?

Yes

### Have you tested this issue on the latest nightly build?

Yes
