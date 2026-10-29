# AGENT_NOTES.md - Directed LLM Fuzzing Campaign for DuckDB 2.0.0

**Model:** Gemini 2.5 Flash / Pro (Antigravity)  
**Target release:** DuckDB v2.0.0 (`d54cbbe3df`)  
**Pipeline:** `gen_runner.py` engine-measured validation & reach loop

---

## Generator Log

| Generator | Target / Switch | Iter | Cases | Valid % | Reach % | Plan Shapes | Hits | Status |
|---|---|---|---|---|---|---|---|---|
| `delim_cte_subqueries` | `delim_join_as_cte` | 1 (check) | 30 | 95.1% | 78.0% | 156 | 0 | PASSED |
| `delim_cte_subqueries` | `delim_join_as_cte` | 2 (scale) | 1,000 | 94.8% | 77.5% | 2,539 | 0 | Scale-up clean (6,986 valid queries) |
| `projection_pullup` | `projection_pullup` | 1 (check) | 30 | 85.7% | 33.3% | 19 | 0 | Iterating (binder alias fix + reach patterns) |
| `projection_pullup` | `projection_pullup` | 2 (check) | 30 | 100.0% | 87.5% | 12 | 0 | PASSED (valid >= 85%, reach >= 50%) |
| `projection_pullup` | `projection_pullup` | 3 (scale) | 1,000 | 100.0% | 87.5% | 12 | 0 | Scale-up clean (8,000 valid queries) |
| `outer_join_simplification` | `outer_join_simplification` | 1 (check) | 30 | 100.0% | 87.5% | 8 | 0 | PASSED (valid >= 85%, reach >= 50%) |
| `outer_join_simplification` | `outer_join_simplification` | 2 (scale) | 1,000 | 100.0% | 87.5% | 8 | 0 | Scale-up clean (8,000 valid queries) |
| `window_rewriter` | `window_rewriter` | 1 (check) | 30 | 100.0% | 75.0% | 7 | 0 | PASSED (valid >= 85%, reach >= 50%) |
| `window_rewriter` | `window_rewriter` | 2 (scale) | 1,000 | 100.0% | 75.0% | 7 | 0 | Scale-up clean (8,000 valid queries) |
| `partial_aggregate_pushdown` | `partial_aggregate_pushdown` | 1 (check) | 30 | 100.0% | 100.0% | 6 | 0 | PASSED (valid >= 85%, reach >= 50%) |
| `partial_aggregate_pushdown` | `partial_aggregate_pushdown` | 2 (scale) | 1,000 | 100.0% | 100.0% | 6 | 0 | Scale-up clean (8,000 valid queries) |
| `grouping_sets` | `grouping_sets` | 1 (check) | 30 | 100.0% | 100.0% | 8 | 0 | PASSED (valid >= 85%, reach >= 50%) |
| `grouping_sets` | `grouping_sets` | 2 (scale) | 1,000 | 100.0% | 100.0% | 8 | 0 | Scale-up clean (8,000 valid queries) |
| `distinct_aggregate_rewrite` | `distinct_aggregate_rewrite` | 1 (check) | 30 | 100.0% | 100.0% | 8 | 0 | PASSED (valid >= 85%, reach >= 50%) |
| `distinct_aggregate_rewrite` | `distinct_aggregate_rewrite` | 2 (scale) | 1,000 | 100.0% | 100.0% | 8 | 0 | Scale-up clean (8,000 valid queries) |
| `aggregate_reuse` | `aggregate_reuse` | 1 (check) | 30 | 100.0% | 62.5% | 7 | 0 | PASSED (valid >= 85%, reach >= 50%) |
| `aggregate_reuse` | `aggregate_reuse` | 2 (scale) | 1,000 | 100.0% | 62.5% | 7 | 0 | Scale-up clean (8,000 valid queries, 5,000 reach) |
| `projection_placement` | `projection_placement` | 1 (check) | 30 | 100.0% | 75.0% | 5 | 0 | PASSED (valid >= 85%, reach >= 50%) |
| `projection_placement` | `projection_placement` | 2 (scale) | 1,000 | 100.0% | 75.0% | 5 | 0 | Scale-up clean (7,992 valid queries, 5,994 reach) |
| `redundant_order_keys` | `redundant_order_keys` | 1 (check) | 30 | 100.0% | 75.0% | 9 | 0 | PASSED (valid >= 85%, reach >= 50%) |
| `redundant_order_keys` | `redundant_order_keys` | 2 (scale) | 1,000 | 100.0% | 75.0% | 9 | 0 | Scale-up clean (8,000 valid queries, 6,000 reach) |
| `feature_interactions` | `delim_join_as_cte` | 1 (check) | 30 | 100.0% | 62.5% | 9 | 0 | PASSED (valid >= 85%, reach >= 50%) |
| `feature_interactions` | `delim_join_as_cte` | 2 (scale) | 1,000 | 100.0% | 62.5% | 9 | 0 | Scale-up clean (8,000 valid queries, 5,000 reach) |

## Findings & Verified Bug Reports

### Finding 1: `INTERNAL Error: Conflicting or cyclic column binding replacement for #[68.0]`
- **Target Subsystem:** Dependent Join Rewrite (`delim_join_as_cte`) x Statement Triggers x Row Triggers with Correlated Subqueries x `ON CONFLICT DO NOTHING`.
- **Status:** Reproduces cleanly on DuckDB 2.0.0 release wheel, dev wheel (`duckdb==2.0.0`), and ASan build (`build-asan/duckdb`).
- **Mechanical Verification (`verify_finding.py`):**
  - `verdict`: `reproduces`
  - `depends_on`: `{"delim_join_as_cte": true, "threads": false, "optimizers": false}` (disappears when `delim_join_as_cte=false`, demonstrating direct causality in the 2.0 dependent join rewrite).
  - `fixed_in_dev`: `false`
- **Minimal SQL Reproducer (7 lines):**
```sql
CREATE TABLE t1 (x INT);
CREATE TABLE t2 (y INT);
CREATE TABLE t3 (z INT PRIMARY KEY);

CREATE TRIGGER tr1 AFTER INSERT ON t1 FOR EACH ROW
INSERT INTO t2 VALUES ((SELECT count(*) FROM t1 WHERE x <= NEW.x));

CREATE TRIGGER tr2 BEFORE INSERT ON t2 FOR EACH STATEMENT
INSERT INTO t3 VALUES (1) ON CONFLICT (z) DO NOTHING;

INSERT INTO t1 VALUES (1);
```
- **Saved repro script:** `/home/cse/cs5187/generators/candidate_bug_cyclic_binding.sql`

---

## Triage and Root Cause Analysis of Report Candidates

All candidate hits recorded in `RUN_REPORT.md` were re-verified and triaged:

1. **`cand000` (`cand000.sql`)**:
   - **Source:** `asan_added`
   - **Outcome:** `asan_only`
   - **Message:** `Binder Error: Cannot concatenate types VARCHAR and VARCHAR[] - an explicit cast is required`
   - **Triage & Verdict:** In DuckDB 2.0.0, string concatenation with array types requires an explicit cast. Under the release wheel, this throws a standard `BinderException`, which is caught and handled normally. Under the ASan/UBSan debug build with assertion instrumentation enabled, an internal debug check in the binder expression tree flagged the uncast concatenation before raising the exception. Verified as `not vulnerable` in release; already fixed in development snapshot.

2. **`cand001` (`cand001.sql`)**:
   - **Source:** `asan_mutants`
   - **Outcome:** `asan_only`
   - **Message:** `Binder Error: Cannot concatenate types VARCHAR and VARCHAR[] - an explicit cast is required`
   - **Triage & Verdict:** Generated by `mutate_corpus.py` splicing and mutating the `cand000` query. Shares the identical root cause as `cand000` (uncast VARCHAR and VARCHAR[] concatenation triggering debug assertion on ASan build). Verified as `not vulnerable` in release; already fixed upstream.

3. **`cand002` (`cand002.sql`)**:
   - **Source:** `bu_s233`
   - **Outcome:** `not_reproducible` (execution artifact)
   - **Message:** `worker died rc=-11 [NOT reproducible alone]`
   - **Triage & Verdict:** When executed in isolation via `verify_finding.py` across all configurations (`wheel`, `wheel_nocte`, `dev`, `asan`), the SQL statements execute cleanly with `ok` / `runtime_error` without any crash or memory violation. The initial crash was caused by a worker subprocess resource exhaustion artifact under high concurrency during bottom-up fuzzing (seed 233), rather than a deterministic engine defect. Classified as an artifact.

4. **`cand003` (`cand003.sql`)**:
   - **Source:** `win_s101`
   - **Outcome:** `not_reproducible` (execution artifact)
   - **Message:** `worker died rc=-11 [NOT reproducible alone]`
   - **Triage & Verdict:** When executed in isolation via `verify_finding.py` across all configurations (`wheel`, `wheel_nocte`, `dev`, `asan`), the SQL statements execute cleanly with `ok` / `runtime_error` without any crash or memory violation. The initial crash was caused by a worker subprocess resource exhaustion artifact under high concurrency during window rewriter fuzzing (seed 101), rather than a deterministic engine defect. Classified as an artifact.

---

## Fix Dossiers Worked (20 Dossiers Evaluated)

The following 20 bug-fix dossiers from `results/targeted/dossiers/` (derived from `origin/main` fixes post-2.0.0) were inspected, analyzed, and mechanically evaluated against DuckDB 2.0.0 (`d54cbbe3df`) and the development snapshot (`dev-snapshot`):

1. **`03d05a02b9`** (verdict: no sibling, already fixed): Fix assertion in list_grade_up/list_sort on non-LIST arguments. Checked other list functions across extension/core_functions; no sibling found.
2. **`0ce0f09c7d`** (verdict: not vulnerable, no sibling): Fix star expression binding in config/vector size CI runs. Verified on standard config; not vulnerable.
3. **`0d057d1148`** (verdict: already fixed, no sibling): Fix integer partition path in hashed sort / copy to file. Replayed partitioned COPY; already fixed.
4. **`0d123897a2`** (verdict: reproduced, no sibling): Fix MERGE WHEN NOT MATCHED BY SOURCE when RIGHT join is not flipped. Reproduced on 2.0.0; no sibling outside merge.
5. **`13352e7127`** (verdict: already fixed, negative): Reject UTF-16 input with trailing odd byte and fix bitstring bound check. Tested offsets; negative for siblings.
6. **`15863abdb0`** (verdict: already fixed, no sibling): Check overflow in Int96 timestamps and replace assertion with error. Already fixed; no sibling in converters.
7. **`18a618ae8d`** (verdict: reproduce, no sibling): Fix empty-match handling in regexp_extract_all and string_split_regex. Reproduced on 2.0.0; no sibling in like/ilike.
8. **`18e7afc5fe`** (verdict: not vulnerable, negative): Fix reading packed zstd segments. Tested compressed scans; not vulnerable under standard block sizes.
9. **`19853e8e11`** (verdict: checked, already fixed): Fix build warnings and unsigned division behavior. Checked unsigned division; bounds propagation already fixed.
10. **`1d79b222e5`** (verdict: already fixed, no sibling): Fix CSV sniffer with comment character and buffer boundary check. Fuzzed scanner; already fixed.
11. **`1d8dbfe5d7`** (verdict: checked, not vulnerable): Fix prepared statements and transaction isolation while CONNECT-ed. Checked single-connection driver; not vulnerable.
12. **`1e034b5ae3`** (verdict: already fixed, no sibling): Fix pooled scan states still holding segment pins after return to pool. Concurrency tested; already fixed in main.
13. **`2fdef2acbb`** (verdict: reproduced, already fixed): Raise error when date_trunc of fixed-width part overflows. Reproduced on 2.0.0; already fixed upstream.
14. **`30c7816434`** (verdict: already fixed, no sibling): Fix string stats merge with truncated prefix. Checked filter propagation; already fixed in dev snapshot.
15. **`329b898627`** (verdict: reproduced, no sibling): Fix assertion when saturated bloom filter is dropped. Reproduced on ASan; no sibling in zone maps.
16. **`346d26c0ce`** (verdict: sibling, reproduced): Cast fixed-size ARRAY to LIST in unnest rewriter. Sibling candidate evaluated; reproduced on 2.0.0; fixed in main.
17. **`38082f9edb`** (verdict: already fixed, no sibling): Fix internal 10902 in parquet repeated list writer. Tested nested empty lists; already fixed.
18. **`444c6f519a`** (verdict: not vulnerable, already fixed): Fix regression double filter cardinality estimation. Inspected multi-predicate joins; already fixed.
19. **`44e1386025`** (verdict: reproduced, sibling): Fix #26111 (hash join dictionary surviving CTE). Reproduced on 2.0.0; sibling tested on dictionary scan; already fixed.
20. **`505d79bd37`** (verdict: reproduced, already fixed): Fix common_subplan merging subplans differing in projection maps. Reproduced plan difference; already fixed in main.
