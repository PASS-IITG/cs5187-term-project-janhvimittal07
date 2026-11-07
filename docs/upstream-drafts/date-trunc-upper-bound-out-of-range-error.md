**Title:** `date_trunc(part, <timestamp col>) = <constant>` near the end of the TIMESTAMP range fails with a conversion error (DateTruncSimplificationRule)

### What happens?

`DateTruncSimplificationRule` (`src/optimizer/rule/date_trunc_simplification.cpp`) rewrites `date_trunc(part, x) = c` into `x >= date_trunc(part, c) AND x < date_trunc(part, c + INTERVAL 1 part)` (and similar for `<>`, `>`, `<=`, `IS [NOT] DISTINCT FROM`). When `c + INTERVAL 1 part` is past the largest timestamp, `CreateTruncAdd` cannot evaluate the bound, and instead of skipping the rewrite it puts the failing expression into the plan:

```cpp
if (trunc->IsFoldable()) {
    Value result;
    if (!ExpressionExecutor::TryEvaluateScalar(rewriter.context, *trunc, result)) {
        return trunc;   // the failing expression ends up in the filter
    }
```

The original query never computes `c + INTERVAL 1 part`, so the error is introduced by the optimizer: a valid query on valid data throws `Conversion Error: Date and time not in timestamp range` (TIMESTAMP) or `Conversion Error: ICU date overflows timestamp range` (TIMESTAMPTZ). With `SET disabled_optimizers = 'expression_rewriter'` the queries return the right result. Operators that only use the lower bound (`<`, `>=`) are not affected.

### To Reproduce

```sql
CREATE TABLE t(x TIMESTAMP);
INSERT INTO t VALUES ('294241-03-01 00:00:00'), ('2024-05-05 10:00:00');

SELECT count(*) FROM t WHERE date_trunc('decade', x) = TIMESTAMP '294240-01-01';
-- Conversion Error: Date and time not in timestamp range
-- expected 1

SELECT count(*) FROM t WHERE date_trunc('century', x) = TIMESTAMP '294200-01-01';   -- same error, expected 1
SELECT count(*) FROM t WHERE date_trunc('decade', x) <> TIMESTAMP '294240-01-01';   -- same error, expected 1
SELECT count(*) FROM t WHERE date_trunc('decade', x) > TIMESTAMP '294240-01-01';    -- same error, expected 0

SET disabled_optimizers = 'expression_rewriter';
SELECT count(*) FROM t WHERE date_trunc('decade', x) = TIMESTAMP '294240-01-01';    -- 1 (correct)
```

```sql
SET TimeZone = 'UTC';
CREATE TABLE tz(x TIMESTAMPTZ);
INSERT INTO tz VALUES ('294247-01-01 00:00:00+00'), ('2024-05-05 10:00:00+00');

SELECT count(*) FROM tz WHERE date_trunc('year', x) = TIMESTAMPTZ '294247-01-01 00:00:00+00';
-- Conversion Error: ICU date overflows timestamp range
-- expected 1 (same for 'quarter' and 'month'); correct with the expression rewriter disabled
```

Returning `nullptr` (no rewrite) when the bound cannot be evaluated would keep the original semantics. This is different from #28527 (sub-day parts on DATE columns, fixed by #28528, which does not touch `CreateTruncAdd`).

### OS:

Linux x86_64 (WSL2)

### DuckDB Version:

2.0.0 (d54cbbe3df); also reproduced on the main nightly build v2.1.0-dev91267 (b3a34f5d69)

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
