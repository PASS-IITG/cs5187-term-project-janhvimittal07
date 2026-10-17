# Week 3 Deliverable: Task N5 — Metamorphic Oracles (NoREC & TLP)

**Author:** Niket (Execution Engine, Build Sanitizers, Differential & Metamorphic Oracles)  
**Date:** October 17, 2026  
**Component:** `src/harness/oracle.py`, `src/harness/sweep_oracle.py`, `src/harness/fuzz.py`, and `src/harness/test_oracles.py`  
**Git Status:** Ready for review and commit  

---

## 1. Executive Summary & Objective

Differential testing (comparing DuckDB across multiple optimizer/thread configurations) detects discrepancies when an optimizer pass alters execution semantics. However, differential testing alone **cannot detect bugs that affect all configurations** (e.g., an incorrect zonemap or filter pushdown logic in the default storage engine that cannot be fully toggled off, or bugs present in unoptimized baseline execution).

To overcome this fundamental limitation, **Task N5** implements two state-of-the-art **metamorphic testing oracles** over arbitrary database schemas:
1. **NoREC (Non-optimizing Reference Engine Construction)** *(Rigger & Su, OSDI 2020)*:
   Translates optimized queries with `WHERE` clauses into semantically equivalent queries using `CASE WHEN` expressions that bypass filter pushdown, index scans, and row-group pruning.
2. **TLP (Ternary Logic Partitioning)** *(Rigger & Su, PLDI 2020)*:
   Partitions the data space of an input query $Q$ into three mutually exclusive, collectively exhaustive subsets according to ternary logic ($P$ is `TRUE`, $P$ is `FALSE`, or $P$ is `NULL`), asserting that the sum/union of the partition queries equals the unpartitioned whole.

These generators inspect table schemas dynamically created during seed execution or fuzzing campaigns, synthesize type-safe deterministic predicates, and run metamorphic checks directly in `sweep_oracle.py`.

---

## 2. Theoretical Foundations

```
                            DATABASE SCHEMA / SEED DATA
                                         │
                    ┌────────────────────┴────────────────────┐
                    ▼                                         ▼
            [ NoREC Oracle ]                           [ TLP Oracle ]
             (OSDI 2020)                                (PLDI 2020)
                    │                                         │
        ┌───────────┴───────────┐                 ┌───────────┴───────────┐
        ▼                       ▼                 ▼                       ▼
   Optimized Query       Reference Query     Whole Query           Partition Queries
   SELECT count(*)       SELECT coalesce(    SELECT count(*)       SELECT coalesce(sum(c))
   FROM T WHERE P;       sum(CASE WHEN P     FROM T;               FROM (
                          THEN 1 ELSE 0                            SELECT count(*) c FROM T WHERE P
   (Exercises filter      END), 0) FROM T;                         UNION ALL
   pushdown, zonemaps,   (Bypasses optimizer                       SELECT count(*) c FROM T WHERE NOT (P)
   row-group pruning)     WHERE evaluation;                        UNION ALL
                          scans all rows)                          SELECT count(*) c FROM T WHERE (P) IS NULL);
        │                       │                 │                       │
        └───────────┬───────────┘                 └───────────┬───────────┘
                    ▼                                         ▼
           Assert: Q_opt == Q_ref                    Assert: Q_whole == Q_parts
                    │                                         │
                    └────────────────────┬────────────────────┘
                                         ▼
                            [ Discrepancy Detected? ]
                                   │           │
                              Yes  │           │ No
                                   ▼           ▼
                           [ Real Logic Bug ] [ Pass ]
```

### 2.1 NoREC Oracle (Non-optimizing Reference Engine Construction)
Given table $T$ and predicate $P$:
- **Optimized Query ($Q_{\text{opt}}$):**
  $$\text{SELECT count(*) FROM } T \text{ WHERE } P$$
  DuckDB pushes down $P$ into the storage scan, applying row-group zonemaps (`row_group_pruner`), column statistics, and vectorized SIMD filter execution.
- **Reference Query ($Q_{\text{ref}}$):**
  $$\text{SELECT coalesce(sum(CASE WHEN } P \text{ THEN 1 ELSE 0 END), 0) FROM } T$$
  Because $P$ is enclosed inside a projection expression (`CASE WHEN`) without a `WHERE` clause, DuckDB is forced to scan every row sequentially without filter pushdown.
- **Metamorphic Invariant:**
  $$Q_{\text{opt}} \equiv Q_{\text{ref}}$$
  Any discrepancy between $Q_{\text{opt}}$ and $Q_{\text{ref}}$ guarantees an optimizer logic defect (such as an incorrect zonemap boundary calculation or an invalid filter pushdown rewrite).

### 2.2 TLP Oracle (Ternary Logic Partitioning)
In SQL three-valued logic, every row evaluated against predicate $P$ must evaluate to `TRUE`, `FALSE`, or `NULL`.
- **Whole Query ($Q_{\text{whole}}$):**
  $$\text{SELECT count(*) FROM } T$$
- **Partition Query ($Q_{\text{parts}}$):**
  $$\text{SELECT coalesce(sum(c), 0) FROM (}$$
  $$\quad\text{SELECT count(*) AS c FROM } T \text{ WHERE } P$$
  $$\quad\text{UNION ALL}$$
  $$\quad\text{SELECT count(*) AS c FROM } T \text{ WHERE NOT (} P \text{)}$$
  $$\quad\text{UNION ALL}$$
  $$\quad\text{SELECT count(*) AS c FROM } T \text{ WHERE (} P \text{) IS NULL)}$$
- **Metamorphic Invariant:**
  $$Q_{\text{whole}} \equiv Q_{\text{parts}}$$

---

## 3. Architecture & Implementation

### 3.1 Schema Introspection (`extract_schema`)
Dynamic schema extraction in `src/harness/oracle.py` queries `information_schema.tables` and `information_schema.columns` on the target database:
- Extracts user base tables and views in the `main` schema.
- System artifacts (`duckdb_*`, `sqlite_*`, `pragma_*`, `pg_*`) are strictly ignored.
- Returns a structured mapping: `{table_name: [{"name": col_name, "type": data_type}, ...]}`.

### 3.2 Canonical Type Classification (`classify_type`)
Maps arbitrary SQL data types to canonical families while properly isolating composite/nested structures:
- `integer`: `INT`, `INTEGER`, `BIGINT`, `HUGEINT`, `SMALLINT`, `TINYINT`, `UBIGINT`, etc.
- `float`: `FLOAT`, `DOUBLE`, `REAL`, `DECIMAL`, `NUMERIC`.
- `string`: `VARCHAR`, `TEXT`, `CHAR`, `STRING`.
- `boolean`: `BOOLEAN`, `BOOL`.
- `date`: `DATE`.
- `timestamp`: `TIMESTAMP`, `TIMESTAMPTZ`.
- `other`: `STRUCT(...)`, `MAP(...)`, `UNION(...)`, `ARRAY`, `LIST[]`.

### 3.3 Type-Aware Deterministic Predicate Generator (`generate_predicates`)
Generates realistic, type-safe predicates designed to trigger optimizer edge cases:
1. **Universal checks**: `col IS NULL`, `col IS NOT NULL`.
2. **Boundary values (DuckDB Vector Boundaries)**:
   DuckDB uses standard vector batches of 2048 rows (`STANDARD_VECTOR_SIZE`). Predicates explicitly test boundary constants:
   `col = 0`, `col != 0`, `col > 0`, `col < 2048`, `col BETWEEN 0 AND 2048`, `col IN (0, 1, 2048)`.
3. **Strings**: `col = ''`, `col != 'test'`, `col LIKE '%a%'`, `length(col) > 0`, `col IN ('', 'a', 'test')`.
4. **Booleans**: `col IS TRUE`, `col IS FALSE`, `NOT col`.
5. **Dates/Timestamps**: `col > DATE '2020-01-01'`, `col <= DATE '1970-01-01'`.
6. **Inter-column comparisons**: `col_a = col_b`, `col_a < col_b` for same-family columns.
7. **Compound boolean logic**: `(P1 AND P2)`, `(P1 OR P2)`, `NOT (P1)`, `((P1 AND P2) OR P3)`.

### 3.4 Safe SQL Identifier Quoting (`_quote_ident`)
To prevent injection errors or reserved keyword collisions (e.g. columns named `"order"`, `"from"`, `"select"`, or spaces in identifiers), all table and column references are escaped using ANSI SQL double-quoting:
```python
def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
```

---

## 4. Pipeline Integration in `sweep_oracle.py`

### CLI Options
`sweep_oracle.py` now supports direct metamorphic fuzzing:
```bash
# Run NoREC checks on all seeds
python src/harness/sweep_oracle.py --workers 4 --norec

# Run TLP checks on all seeds
python src/harness/sweep_oracle.py --workers 4 --tlp

# Run both NoREC and TLP checks
python src/harness/sweep_oracle.py --workers 4 --metamorphic --metamorphic-limit 5

# Replay single seed with metamorphic checks
python src/harness/sweep_oracle.py --one corpus/seeds/test.sql --metamorphic
```

### Execution Flow in `run_seed`
1. Replays seed statements across all configurations.
2. If `--norec` or `--tlp` is enabled and `setup_tainted` is False (respecting Gate 2 noise tracking):
   - Introspects user tables created by the seed.
   - For each table, generates up to `--metamorphic-limit` predicates.
   - Executes NoREC and TLP pairs on the live database.
   - Discrepancies are flagged with `kind="norec"` or `kind="tlp"` and stored in `found`.
   - Results are counted under `norec_mismatches` and `tlp_mismatches`.

---

## 5. Automated Test Suite & Verification Results

A comprehensive test suite was implemented in `src/harness/test_oracles.py`:

```bash
FUZZ_WORK=/Users/honey0025/fuzzwork python src/harness/test_oracles.py -v
```

### Test Results
```text
test_injected_mismatch_detection (__main__.TestOracles.test_injected_mismatch_detection)
Verify that when an intentional mismatch occurs, DiffResult flags mismatch=True. ... ok
test_norec_equivalence (__main__.TestOracles.test_norec_equivalence)
NoREC: count(*) and sum(val) under WHERE P must equal unoptimized CASE WHEN. ... ok
test_predicate_generation (__main__.TestOracles.test_predicate_generation)
generate_predicates produces diverse, type-safe, parseable predicates. ... ok
test_schema_extraction (__main__.TestOracles.test_schema_extraction)
extract_schema must identify user tables and column types while ignoring system catalogs. ... ok
test_sweep_oracle_seed_integration (__main__.TestOracles.test_sweep_oracle_seed_integration)
Integration test: sweep_oracle.run_seed executes metamorphic oracles over seed schemas. ... ok
test_tlp_equivalence (__main__.TestOracles.test_tlp_equivalence)
TLP: Whole query must equal the union of partitions (TRUE, FALSE, NULL). ... ok
test_type_classification (__main__.TestOracles.test_type_classification)
classify_type correctly maps SQL types to canonical families. ... ok

----------------------------------------------------------------------
Ran 7 tests in 0.097s

OK
```

### Full Test Suite Regression Status
1. `src/harness/test_oracles.py`: **7/7 passed** (0.097s)
2. `src/harness/test_noise_gates.py`: **6/6 passed** (0.398s, all 6 noise gates verified)
3. `src/driver/test_driver.py`: **5/5 passed** (1.352s, 547–845 QPS verified)

---

## 6. Deliverables Checklist

| Deliverable | Location | Status |
|---|---|---|
| Schema Introspection | `src/harness/oracle.py` (`extract_schema`) | **Done** |
| Type Classification | `src/harness/oracle.py` (`classify_type`) | **Done** |
| Predicate Generation | `src/harness/oracle.py` (`generate_predicates`) | **Done** |
| NoREC Generator & Checker | `src/harness/oracle.py` (`generate_norec_pairs`, `check_norec`) | **Done** |
| TLP Generator & Checker | `src/harness/oracle.py` (`generate_tlp_pairs`, `check_tlp`) | **Done** |
| Sweep Oracle Integration | `src/harness/sweep_oracle.py` (`--norec`, `--tlp`, `--metamorphic`) | **Done** |
| Pipeline Candidates Fix | `src/harness/fuzz.py` (safe post-seed query indexing) | **Done** |
| Test Suite | `src/harness/test_oracles.py` (7 tests, 100% pass) | **Done** |
| Documentation | `src/docs/week3-n5-validation.md` | **Done** |
