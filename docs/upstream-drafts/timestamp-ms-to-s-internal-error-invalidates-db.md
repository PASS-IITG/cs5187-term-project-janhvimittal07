**Title:** Casting a `TIMESTAMP_MS` at the end of the range to `TIMESTAMP_S` raises INTERNAL Error "Unrecognized type for ConvertToString INT64" and invalidates the database

### What happens?

Casting a `TIMESTAMP_MS` value whose rounding to seconds goes past the largest timestamp to `TIMESTAMP_S` fails, as intended since #26628. But building the error message fails because `TIMESTAMP_MS` has no string conversion in `CastExceptionText` (stack: `VectorCastHelpers::TryCastLoop<timestamp_ms_t, timestamp_sec_t>` -> `CastExceptionText<timestamp_ms_t, timestamp_sec_t>`). The query raises an INTERNAL Error instead of a Conversion Error, and the database is invalidated: every following statement fails with `FATAL Error: database has been invalidated`.

The test added in #26628 (`test/sql/types/timestamp/timestamp_round_out_of_range.test`) only casts from `TIMESTAMP` (microseconds), so the `TIMESTAMP_MS` source path is not covered.

It is also reachable without such a value in the query. For a `TIMESTAMP_MS` column that holds only `infinity`, the column statistics have a finite minimum (`stats(x)` shows `'min': '294247-01-10 04:00:54.775'`, `'max': infinity`). Statistics propagation folds the cast on that minimum and hits the same error, so even a plain `SELECT CAST(x AS TIMESTAMP_S) FROM t` (and its `EXPLAIN`) crashes the database. With `SET disabled_optimizers = 'statistics_propagation'` that query returns `infinity` as expected.

### To Reproduce

```sql
SELECT CAST(TIMESTAMP_MS '294247-01-10 04:00:54.775' AS TIMESTAMP_S);
-- INTERNAL Error: Unrecognized type for ConvertToString INT64
-- expected: Conversion Error (as for TIMESTAMP '294247-01-10 04:00:54.775806'::TIMESTAMP_S)
SELECT 42;
-- FATAL Error: Failed: database has been invalidated because of a previous fatal error.
```

```sql
CREATE TABLE t(x TIMESTAMP_MS);
INSERT INTO t VALUES ('infinity');
SELECT stats(x) FROM t;
-- {'approx_unique': 1, 'has_no_null': true, 'has_null': false, 'max': infinity, 'min': '294247-01-10 04:00:54.775'}
-- min should be infinity
SELECT CAST(x AS TIMESTAMP_S) FROM t;
-- INTERNAL Error: Unrecognized type for ConvertToString INT64 (database invalidated)
-- expected: infinity
```

```sql
-- works when statistics propagation is disabled
CREATE TABLE t(x TIMESTAMP_MS);
INSERT INTO t VALUES ('infinity');
SET disabled_optimizers = 'statistics_propagation';
SELECT CAST(x AS TIMESTAMP_S)::VARCHAR FROM t;   -- infinity
```

`-infinity`, and `TIMESTAMP` / `TIMESTAMP_NS` columns holding `infinity`, are not affected.

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
