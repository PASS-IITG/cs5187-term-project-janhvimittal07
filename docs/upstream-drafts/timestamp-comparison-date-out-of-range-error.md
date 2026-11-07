**Title:** `CAST(<timestamp col> AS DATE) = <DATE>` fails with a conversion error at the TIMESTAMP range limits (TimeStampComparison)

### What happens?

`CAST(ts AS DATE) = d` on a TIMESTAMP column is rewritten by `TimeStampComparison` (`src/optimizer/rule/timestamp_comparison.cpp`) into `ts >= TIMESTAMP(d) AND ts < TIMESTAMP(d + 1 day)`. The bounds are built with `Value::TIMESTAMP(...)` / `GetValue<timestamp_t>()` without checking that `d` and `d + 1` are inside the TIMESTAMP range. Because DATE has a much wider domain than TIMESTAMP, the query fails instead of returning a result:

* `d` is the last day TIMESTAMP can hold (`294247-01-10`): `d + 1` is out of range, and the query throws even though the table has matching rows (expected: those rows).
* `d` is outside the TIMESTAMP range (e.g. `1000000-01-01`, `290309-12-21 (BC)`): the query throws `Conversion Error: Date and time not in timestamp range` (expected: no rows, since no timestamp can be on that date).

With `SET disabled_optimizers = 'expression_rewriter'` all of these return the right answer. The same class of problem was fixed for the monotone-preimage rule in #27082 ("keep TIMESTAMP bounds inside the convertible range"); this rule has no such guard (it only handles infinite dates and a failing `d + 1` in DATE arithmetic).

### To Reproduce

```sql
CREATE TABLE t(x TIMESTAMP);
INSERT INTO t VALUES (TIMESTAMP '294247-01-10 00:00:00'), (TIMESTAMP '294247-01-10 04:00:54.775806'), (TIMESTAMP '2024-01-01 10:00:00');

SELECT count(*) FROM t WHERE CAST(x AS DATE) = DATE '294247-01-10';
-- Invalid Input Error: Type INT32 with value 294247-01-11 can't be cast to the destination type INT64
-- expected 2

SELECT count(*) FROM t WHERE CAST(x AS DATE) = DATE '1000000-01-01';
-- Conversion Error: Date and time not in timestamp range
-- expected 0

SELECT count(*) FROM t WHERE CAST(x AS DATE) = DATE '290309-12-21 (BC)';
-- Conversion Error: Date and time not in timestamp range
-- expected 0

SET disabled_optimizers = 'expression_rewriter';
SELECT count(*) FROM t WHERE CAST(x AS DATE) = DATE '294247-01-10';     -- 2 (correct)
SELECT count(*) FROM t WHERE CAST(x AS DATE) = DATE '1000000-01-01';    -- 0 (correct)
SELECT count(*) FROM t WHERE CAST(x AS DATE) = DATE '290309-12-21 (BC)'; -- 0 (correct)
```

It also happens inside `FILTER (WHERE ...)`, `NOT (...)`, join conditions and `CASE`.

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
