# Week 1 — N2: Pinned-Main Validation & Upstream Comparison

## 1. Overview & Motivation

Task **N2** requires adding a build of DuckDB `main` pinned to a specific upstream commit, and integrating a `"reproduces on main?"` verification step into the triage pipeline (`triage.py`).

**Why this was introduced:**
During initial differential sweeps on DuckDB v1.5.6, the team discovered two real crashes with disabled optimizer configurations:
1. **ASOF join crash** (`SIGSEGV` when joining with a non-comparison condition and `join_order` disabled):
   - Tracked upstream as **Issue #26300**.
   - Fixed by DuckDB maintainers in **PR #26301** (merged 2026-10-08).
   - **Triage result on pinned `main`:** `reproduces_on_main = false` (properly identified as already fixed upstream).
2. **MERGE INTO internal error** (`INTERNAL Error: Failed to bind column reference rowid` when using `WHEN NOT MATCHED BY SOURCE` and `build_side_probe_side` disabled):
   - Tracked upstream in **Fix PR #26715** (open).
   - Demonstrates how optimizer configuration sweeps can surface edge-case binder and planner defects.

Without an automated check against DuckDB's latest development branch (`main`), team members would spend hours manually filing duplicate bug reports for issues already fixed upstream.

---

## 2. Pinned Build Environment

- **Pinned DuckDB revision:** `690d113c24dad3b37529c1518354bcbe00d8ffbc`
- **Build script:** `src/build-main.sh`
- **Binary location:** `~/fuzzwork/build-main/duckdb`
- **Comparison baseline:** `~/fuzzwork/build-asan/duckdb` (v1.5.6 with ASan+UBSan)
- **Smoke test:** `SELECT 1 + 1;` returns `2`.

---

## 3. Validation Cases

### Case A: The Motivating ASOF Join (#26300 / PR #26301)
- **SQL Reproducer:** [`~/fuzzwork/reproducers/asof_issue_26300.sql`](file:///Users/honey0025/fuzzwork/reproducers/asof_issue_26300.sql)
  ```sql
  SET disabled_optimizers='join_order';
  CREATE TABLE a_t AS SELECT (range % 3)::INTEGER AS k, ['sad', 'ok', 'happy'][1 + range % 3] AS m, range::INTEGER AS t FROM range(12);
  CREATE TABLE b_t AS SELECT (range % 3)::INTEGER AS k, range::INTEGER AS t FROM range(12);
  SELECT count(*) FROM a_t ASOF JOIN b_t ON a_t.k = b_t.k AND a_t.t >= b_t.t AND a_t.m IN ('sad', 'ok', 'happy');
  ```
- **Checker Finding ID:** `af670599ce12`
- **Triage command:** `python harness/main_triage.py ~/fuzzwork/reproducers/asof_issue_26300.sql`
- **Side-by-Side Comparison:**
  - **v1.5.6 (`build-asan`):** **CRASH / UndefinedBehaviorSanitizer violation** (`rc = -6` / `exit code 134`):
    ```text
    duckdb/src/function/table/table_scan.cpp:819:33: runtime error: member call on null pointer of type 'duckdb::FunctionData'
    SUMMARY: UndefinedBehaviorSanitizer: undefined-behavior table_scan.cpp:819:33
    ```
    *Outcome classified:* `asan` (crash).
  - **Pinned `main` (`build-main` @ `690d113c`):** **CLEAN EXECUTION** (`rc = 0`, duration: 0.52s):
    Returns expected count table (`12`).
    *Outcome classified:* `ok`.
- **Triage classification:** `reproduces_on_main = false`
- **Significance:** Proves the bug is fixed on upstream `main` via PR #26301. Automated triage suppresses false duplicate bug reports.
- **Evidence directory:** `~/fuzzwork/results/triage/af670599ce12/` (`meta.json`, `asan.stderr.txt`, `main.stdout.txt`).

### Case B: MERGE INTO RowID Binding (PR #26715)
- **SQL Reproducer:** [`~/fuzzwork/reproducers/merge_pr_26715.sql`](file:///Users/honey0025/fuzzwork/reproducers/merge_pr_26715.sql)
  ```sql
  SET disabled_optimizers='build_side_probe_side';
  CREATE TABLE d(id INT, v INT);
  INSERT INTO d VALUES (1,10),(2,20);
  CREATE TABLE s(id INT, v INT);
  INSERT INTO s VALUES (1,100);
  MERGE INTO d USING s ON s.id=d.id
    WHEN NOT MATCHED BY SOURCE THEN UPDATE SET v=d.v+1;
  ```
- **Checker Finding ID:** `246fad5901cf`
- **Triage command:** `python harness/main_triage.py ~/fuzzwork/reproducers/merge_pr_26715.sql`
- **Side-by-Side Comparison:**
  - **v1.5.6 (`build-asan`):** **INTERNAL ERROR** (`rc = 1`, duration: 0.11s):
    ```text
    INTERNAL Error: Failed to bind column reference "rowid" [0.2] (bindings: {#[1.0], #[4.0], #[4.2]})
    ```
    *Outcome classified:* `internal_error`.
  - **Pinned `main` (`build-main` @ `690d113c`):** **STILL REPRODUCES** (`rc = 1`, duration: 0.19s):
    ```text
    INTERNAL Error: Failed to bind column reference "rowid" [0.2] (bindings: {#[1.0], #[4.0], #[4.2]})
    ```
    *Outcome classified:* `internal_error`.
- **Triage classification:** `reproduces_on_main = true`
- **Significance:** Demonstrates a true bug reproduction across both release and developmental commits. PR #26715 merged on 12 Oct (after pinned commit `690d113c` from 9 Oct), so the defect is legitimately caught and flagged for upstream tracking!
- **Evidence directory:** `~/fuzzwork/results/triage/246fad5901cf/` (`meta.json`, `asan.stderr.txt`, `main.stderr.txt`).

### Case C: Automated Pipeline Integration Test (`results/triage/0c5a16d788`)
- **Saved finding tested:** `results/triage/0c5a16d788/repro.sql`
- **Checker finding ID:** `6df259442da8`
- **ASan outcome:** `binder_error`
- **Pinned-main outcome:** `binder_error`
- **Execution duration on main:** ~0.20s
- **Triage classification:** `reproduces_on_main = false`
- **Evidence:** `~/fuzzwork/results/triage/6df259442da8/`

---

## 4. Pipeline Integration Status

The check is integrated directly into the core triage pipeline:
- Function: `_check_on_main()` in [`harness/triage.py`](file:///Users/honey0025/duckdb-term-project/cs5187-term-project-janhvimittal07/src/harness/triage.py).
- Output: Automatically populates the `main_build` metadata and adds the `main build` column in [`results/REPORT.md`](file:///Users/honey0025/fuzzwork/results/REPORT.md).
- Status across 33 existing sweep findings: Verified and documented.
