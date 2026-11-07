**Title:** INTERNAL Error "Information loss on integer cast" for `ubigint_col + c1 = c2` when a constant is above the BIGINT maximum (IntegralValue::Get for UBIGINT)

### What happens?

A comparison of `x + c1`, `x - c1` or `x * c1` on a `UBIGINT` column with a constant fails with an INTERNAL Error when one of the constants (or the folded result) is larger than `9223372036854775807`, even though the query is valid and the data does not overflow. With `SET disabled_optimizers = 'expression_rewriter'` the queries return the correct result.

The rewrite is `MoveConstantsRule` (`src/optimizer/rule/move_constants.cpp`), which reads both constants with `IntegralValue::Get`. For `UBIGINT` that function converts through `int64_t`:

```cpp
// src/common/types/value.cpp, IntegralValue::Get
case PhysicalType::UINT64:
    return NumericCast<int64_t>(UBigIntValue::Get(value));
```

so every `UBIGINT` value above `INT64_MAX` raises `InternalException("Information loss on integer cast ...")`, although the result type `hugeint_t` can hold it. `UINTEGER`, `HUGEINT` and `UHUGEINT` columns are not affected.

### To Reproduce

```sql
CREATE TABLE t(x UBIGINT);
INSERT INTO t VALUES (0), (1);

SELECT count(*) FROM t WHERE x + 1 = 18446744073709551615;
-- INTERNAL Error: Information loss on integer cast: value 18446744073709551615 outside of target range [-9223372036854775808, 9223372036854775807]
-- expected 0

SELECT count(*) FROM t WHERE x + 18446744073709551613 = 18446744073709551614;
-- same INTERNAL Error, expected 1

SELECT count(*) FROM t WHERE x * 18446744073709551613 = 18446744073709551613;
-- same INTERNAL Error, expected 1

SELECT count(*) FROM t WHERE x + 9223372036854775807 = 9223372036854775808;
-- same INTERNAL Error, expected 1

SET disabled_optimizers = 'expression_rewriter';
SELECT count(*) FROM t WHERE x + 1 = 18446744073709551615;                  -- 0 (correct)
SELECT count(*) FROM t WHERE x + 18446744073709551613 = 18446744073709551614; -- 1 (correct)
```

When the original expression would overflow on the data, the user gets this INTERNAL Error instead of the expected `Out of Range Error: Overflow in addition of UINT64`.

### OS:

Linux x86_64 (WSL2)

### DuckDB Version:

2.0.0 (d54cbbe3df); also reproduced on the main nightly build v2.1.0-dev91267 (b3a34f5d69), where `IntegralValue::Get` is unchanged

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
