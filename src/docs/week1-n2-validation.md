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
- **SQL Reproducer:**
  ```sql
  SET disabled_optimizers='join_order';
  CREATE TABLE tt1 (i INTEGER, j VARCHAR);
  INSERT INTO tt1 VALUES (2, 'A'), (4, 'B'), (5, 'A');
  CREATE TABLE tt2 (i INTEGER, j VARCHAR, k VARCHAR);
  INSERT INTO tt2 VALUES (1, 'A', 'I'), (3, 'B', 'II');
  SELECT tt1.i, tt2.k FROM tt1 ASOF JOIN tt2 ON (tt1.j = tt2.j OR tt1.j = tt2.j) AND tt1.i >= tt2.i ORDER BY tt1.i;
  ```
- **v1.5.6 (Unpatched):** Triggered `SIGSEGV` in optimizer join ordering.
- **Pinned `main` (`690d113c`):** Exits cleanly with code 0.
- **Triage classification:** `reproduces_on_main = false` (Fixed upstream).

### Case B: MERGE INTO (`WHEN NOT MATCHED BY SOURCE` / PR #26715)
- **SQL Reproducer:**
  ```sql
  SET disabled_optimizers='build_side_probe_side';
  CREATE TABLE Stock(item_id int, balance int);
  INSERT INTO Stock VALUES (5, 10), (10, 20), (20, 30);
  MERGE INTO Stock USING (VALUES (5, 20), (10, 30)) new_accounts(item_id, balance) USING (item_id)
  WHEN MATCHED THEN UPDATE
  WHEN NOT MATCHED BY TARGET THEN INSERT
  WHEN NOT MATCHED BY SOURCE THEN DELETE;
  ```
- **Context:** Exercises complex rowid binding when probe-side optimizer passes are disabled.

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
