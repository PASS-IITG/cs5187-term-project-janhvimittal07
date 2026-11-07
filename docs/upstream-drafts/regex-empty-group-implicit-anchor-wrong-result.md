**Title:** `regexp_matches(col, '(?:)abc')` returns wrong results: an empty group at the start/end of the pattern is treated as an anchor (RegexOptimizationRule)

### What happens?

`RegexOptimizationRule` (`src/optimizer/rule/regex_optimizations.cpp`) turns simple patterns into `LIKE`-style checks (`contains`, `prefix`/range, `suffix`). In `LikeMatchFromRegex`, which converts a concatenation, a leading `%` is only added when the literal is the first element (`if (cur_sub_index == 0) ret.like_string += "%";`) and a trailing `%` only when it is the last. An empty non-capturing group `(?:)` is parsed as `kRegexpEmptyMatch`, which the conversion skips (`case duckdb_re2::kRegexpEmptyMatch: break;`) but which still occupies position 0 (or the last position). So the literal is no longer first/last, the `%` is dropped, and the pattern becomes anchored:

* `(?:)abc` is rewritten to `s >= 'abc' AND s < 'abd'` (starts with `abc`)
* `abc(?:)` is rewritten to `suffix(s, 'abc')`

The pattern has no anchors, so rows that contain `abc` elsewhere are dropped (in `WHERE`) or get `false` (in the `SELECT` list). With `SET disabled_optimizers = 'expression_rewriter'`, and for constant inputs, the result is correct.

### To Reproduce

```sql
CREATE TABLE t(s VARCHAR);
INSERT INTO t VALUES ('abc'), ('xabc'), ('abcx');

SELECT s, regexp_matches(s, '(?:)abc') AS a, regexp_matches(s, 'abc(?:)') AS b, regexp_matches(s, 'abc') AS c
FROM t ORDER BY s;
-- abc   true  true  true
-- abcx  true  false true    <- b should be true
-- xabc  false true  true    <- a should be true

SELECT count(*) FROM t WHERE regexp_matches(s, '(?:)abc');
-- 2, expected 3

SET disabled_optimizers = 'expression_rewriter';
SELECT s, regexp_matches(s, '(?:)abc') AS a, regexp_matches(s, 'abc(?:)') AS b FROM t ORDER BY s;
-- all true (correct)
```

Nested empty groups (`(?:(?:))abc`) behave the same; quantified ones (`(?:x){0}abc`, `(?:)+abc`) and empty groups in the middle (`a(?:)bc`) are fine.

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
