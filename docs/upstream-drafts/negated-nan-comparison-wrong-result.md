<!-- FILED 2026-10-29 05:08 UTC by Prabhnoor: https://github.com/duckdb/duckdb/issues/28052 (label at filing: needs triage) -->
# DRAFT - NOT FILED - found 2026-10-25 15:04 UTC by the direct-authoring arm (Opus agent), verified by Claude on 2026-10-29
# CONFIRMED still wrong on main 6287a4e595 (v2.1.0-alpha51401, nightly CI build of 2026-10-29). BEFORE FILING: (1) repeat the duplicate search that day, (2) team agrees.

Suggested title:
Wrong result: comparing a negated DOUBLE column with a NaN constant returns all rows (regression vs v1.5.6; correct with expression_rewriter disabled)

### What happens?

`WHERE -d > 'nan'::DOUBLE` returns every row, including the NaN row, on v2.0.0. DuckDB orders NaN above every other value
(including infinity), so `-d > NaN` is false for every row and the query should return no rows. v1.5.6 returns no rows, and so
does v2.0.0 with `SET disabled_optimizers='expression_rewriter'`. The mirrored query `-d < 'nan'::DOUBLE` returns no rows on
2.0.0 but should return every non-NaN row.

### To Reproduce

```sql
CREATE TABLE f(d DOUBLE);
INSERT INTO f VALUES (1.5), (-2.5), ('nan'), ('inf');
SELECT d FROM f WHERE -d > 'nan'::DOUBLE ORDER BY d;   -- 2.0.0: -2.5, 1.5, inf, nan   expected: no rows
SELECT d FROM f WHERE -d < 'nan'::DOUBLE ORDER BY d;   -- 2.0.0: no rows               expected: -2.5, 1.5, inf
SET disabled_optimizers = 'expression_rewriter';
SELECT d FROM f WHERE -d > 'nan'::DOUBLE ORDER BY d;   -- no rows (correct)
SELECT d FROM f WHERE -d < 'nan'::DOUBLE ORDER BY d;   -- -2.5, 1.5, inf (correct)
```

### Versions (measured 2026-10-29 on Linux x86_64, WSL2)

| build | `-d > nan` result |
|---|---|
| v1.5.6 wheel | no rows (correct) |
| v2.0.0 wheel (source d54cbbe3df) | 4 rows (wrong) |
| 2.0.1.dev2610211350 nightly wheel | 4 rows (wrong) |
| main 48cca2f7a3 (23 Oct, assertions build) | 4 rows (wrong) |
| main 6287a4e595 = v2.1.0-alpha51401 (CI nightly CLI build, 2026-10-29 00:49 UTC) | 4 rows (wrong); the mirrored query returns no rows (wrong); with expression_rewriter disabled both are correct |

### Notes for the reporter (delete before filing)
* Cause (from reading origin/main, not verified by a debugger): MoveUnaryMinusRule::Apply in src/optimizer/rule/move_constants.cpp. The NaN guard
  added on 2026-07-07 (commit b786cdffc2a "guard against nan"; possibly part of merged PR #24368 "Fix two wrong-results bugs in the expression rewriter", not checked)
  handles a NaN column VALUE: [-x > c] becomes [x < -c OR isnan(x)]. It does not handle a NaN CONSTANT: with c = NaN, -c is NaN, and
  [x < NaN OR isnan(x)] is true for every row, while [-x > NaN] is false for every row (NaN sorts above everything). A check for a NaN
  constant in the floating-point branch (return nullptr) would avoid the rewrite. The file is unchanged between main 48cca2f7a3 and 6287a4e595.
* Sibling shapes the agent reported (not re-verified by Claude): `-d >= nan`, `-d <= nan`, FLOAT columns, `nan < -d`.
* Duplicate search (2026-10-29): full-text scan of 1,865 issues/PRs updated since 10 Oct, plus gh searches for NaN, isnan, MoveUnaryMinusRule, move_constants, unary minus, negation: nothing about a negated
  comparison with a NaN constant. NaN-related items are about Parquet, fsum, timestamps, statistics (e.g. #25521, #27696, #27969, #27945), not this.
* AI use: the query was found by an LLM-directed differential run (optimizers on vs off); Claude verified it on the builds above.
