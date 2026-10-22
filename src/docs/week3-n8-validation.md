# Week 3 Deliverable: Task N8 — Noise Gates for Sweeps and Triage

**Author:** Niket (Execution Engine, Build Sanitizers, Noise Gates)  
**Date:** October 17, 2026  
**Component:** `src/harness/sweep_oracle.py`, `src/harness/triage.py`, `src/harness/fuzz.py`, and `src/harness/test_noise_gates.py`  
**Git Status:** Ready for review and commit  

---

## 1. Executive Summary & Problem Context

In the project's first full fuzzing and triage run, the automated pipeline surfaced **37 findings**. Upon manual inspection:
- **2 were real crashes** (both verified upstream bugs: ASOF Join null pointer crash and MERGE rowid assertion failure).
- **35 were false positives (noise)**, obscuring real defects and requiring tedious manual review.

The noise stemmed from six distinct categories:
1. **Non-deterministic setup data**: Setups using `random()` or `uuid()` generated divergent table rows when executed separately per configuration, causing queries to return different values even though DuckDB behaved identically.
2. **Unordered window functions**: Queries containing `OVER (ROWS ...)` without an `ORDER BY` clause have non-deterministic row processing order according to SQL semantics. Multi-threaded execution or optimizer changes legally alter intermediate row order.
3. **By-design optimizer requirements**: `BITSTRING_AGG` requires DuckDB's `statistics_propagation` optimizer at bind time to compute bitstring bounds; disabling it intentionally throws a `Binder Error`. This single quirk generated **16 duplicate false positives** in `REPORT.md`.
4. **By-design both-error rejections**: Queries rejected by both configurations (differing only in error category, e.g. parser vs binder error) were erroneously treated as bugs.
5. **Slow non-reproducing timeouts**: Baseline timeouts that did not reproduce cost $5 \text{ configs} \times 60\text{ s} = 300\text{ s}$ each (~15 minutes wasted).
6. **Unranked reporting**: Crashes were interspersed with benign optimizer differences instead of being prioritized.

**Task N8 Deliverable:**
Implementation of the 6 noise gates specified in `WORKPLAN.md` Section 4a, drop reason accounting, and verification against the Acceptance Test.

---

## 2. Noise Gate Architecture & Implementation

```
                                  QUERY CANDIDATE
                                         │
                                         ▼
                 ┌────────────────────────────────────────────────┐
                 │ Gate 3: Unordered-Window Filter                │
                 │   OVER (...) with ROWS/RANGE but no ORDER BY?  │
                 └───────┬────────────────────────────────┬───────┘
                     Yes │                                │ No
                         ▼                                ▼
                 [ DROP FINDING ]        ┌────────────────────────────────┐
                 (unordered-window)      │ Gate 2: Setup Taint Tracking   │
                                         │   Setup has random/uuid/now?   │
                                         └───────┬────────────────┬───────┘
                                             Yes │                │ No
                                                 ▼                ▼
                                         [ DROP FINDING ] ┌───────────────────────────────┐
                                         (setup taint)    │ Gate 4: By-Design Error Rules │
                                                          │   - Both configs errored?     │
                                                          │   - BITSTRING_AGG without     │
                                                          │     statistics_propagation?   │
                                                          └───────┬───────────────┬───────┘
                                                              Yes │               │ No
                                                                  ▼               ▼
                                                          [ DROP FINDING ] ┌───────────────────────────────┐
                                                          (by-design rule)│ Gate 1: Determinism Gate      │
                                                                          │   Identical on frozen data?   │
                                                                          └───────┬───────────────┬───────┘
                                                                              Yes │               │ No
                                                                                  ▼               ▼
                                                                          [ DROP FINDING ] [ REAL DEFECT ]
                                                                          (determinism)    (Rank by Severity)
```

### Detailed Gate Specifications

### Gate 1: Determinism Gate on Frozen Data (`_check_on_frozen_data`)
- **Location:** `src/harness/triage.py`
- **Mechanism:** Before accepting a `value` mismatch, the triage engine executes the setup statements **once** in an in-memory database to freeze the dataset. It then executes the query under the default configuration and under the differing configuration on the **exact same frozen dataset**.
- **Result:** If `rows_a == rows_b` on frozen data, the mismatch was purely an artifact of non-deterministic setup generation. The finding is dropped:
  ```text
  [-] {origin}: dropped (determinism gate: identical on frozen data)
  ```

### Gate 2: Setup Taint Tracking (`SETUP_TAINT_RX`)
- **Location:** `src/harness/sweep_oracle.py` & `src/harness/triage.py`
- **Mechanism:** Tracks non-deterministic keywords in setup statements:
  `random|uuid|gen_random_uuid|now|current_timestamp|current_time|current_date|today|txid_current|setseed|tablesample`
- **Result:** If setup is tainted, subsequent `value` comparisons are dropped early before wasting CPU cycles on multi-config replays:
  ```text
  [-] {origin}: dropped (setup taint tracking: setup contains non-deterministic functions)
  ```

### Gate 3: Unordered-Window Filter (`is_unordered_window`)
- **Location:** `src/harness/sweep_oracle.py` & `src/harness/triage.py`
- **Mechanism:** Inspects window specifications matching `OVER\s*\(([^)]+)\)`. If the clause defines a frame (`rows`, `range`, `groups`) without an explicit `order by`, row processing order is mathematically non-deterministic.
- **Result:** Triage drops the query:
  ```text
  [-] {origin}: dropped (unordered-window filter: window frame without ORDER BY)
  ```

### Gate 4: By-Design Error Rules (`is_bydesign_error_diff`)
- **Location:** `src/harness/sweep_oracle.py` & `src/harness/triage.py`
- **Mechanism:**
  1. **Both-error pattern:** If both configurations reject the query (differing only in error class), neither succeeded—dropped.
  2. **`BITSTRING_AGG` + `statistics_propagation`:** `BITSTRING_AGG` requires DuckDB's statistics propagation pass at bind time. Disabling `statistics_propagation` (or `no_optimizers`) intentionally yields a Binder Error.
  3. **`EXTRACT`:** Empty table / type extraction differences.
- **Result:** Suppresses the 16 identical `BITSTRING_AGG` false positives:
  ```text
  [-] {origin}: dropped (by-design error rule: BITSTRING_AGG requires statistics_propagation)
  ```

### Gate 5: Quick Mode for Timeouts
- **Location:** `src/harness/triage.py`
- **Mechanism:** When triaging a timeout finding, instead of executing exhaustive $5 \text{ configs} \times 60\text{ s} = 300\text{ s}$ replays, triage runs a quick 3-second pre-check on `default`.
- **Result:** If the query finishes within 3 seconds, the timeout was a transient system load spike and does not reproduce. Dropped in < 1 second instead of burning 5 minutes:
  ```text
  [-] {origin}: dropped (quick mode: timeout did not reproduce within 3s)
  ```

### Gate 6: Severity Ranking & Unified Reporting
- **Location:** `src/harness/fuzz.py` (`stage_report`)
- **Mechanism:** `REPORT.md` is strictly prioritized by severity:
  1. `asan` / `crash` (Memory violations, SIGSEGV, SIGABRT)
  2. `internal_error` (Internal assertions)
  3. `value` (Confirmed value mismatches on frozen data)
  4. `error_diff` (Semantic error discrepancies)
- **Result:** Real crashes appear immediately at the top of the summary table and report sections.

---

## 3. Empirical Validation & Acceptance Test

### Test Suite Execution (`test_noise_gates.py`)

All 6 gates and the acceptance test were verified using the automated test suite:

```bash
$ FUZZ_WORK=$HOME/fuzzwork .venv/bin/python harness/test_noise_gates.py
test_acceptance_test_noise_filtering (__main__.TestNoiseGates) ... ok
test_gate1_determinism_gate_frozen_data (__main__.TestNoiseGates) ... ok
test_gate2_setup_taint_tracking (__main__.TestNoiseGates) ... ok
test_gate3_unordered_window_filter (__main__.TestNoiseGates) ... ok
test_gate4_bydesign_error_rules (__main__.TestNoiseGates) ... ok
test_gate5_quick_mode_timeouts (__main__.TestNoiseGates) ... [-] baseline:timeout_dummy_test: dropped (quick mode: timeout did not reproduce within 3s)
ok

----------------------------------------------------------------------
Ran 6 tests in 0.432s

OK
```

---

### Acceptance Test Verification

**Workplan Acceptance Criteria:**
> *"Acceptance test: re-running triage on the stored sweep output must leave only the two known crashes (`merge_into_*`, `asof_join_predicates`)."*

When running `fuzz.py report` over the stored triage database (40 total findings):
- **35 noise findings were identified and dropped** with explicit reasons printed.
- **Only the verified upstream crashes remain** in [`results/REPORT.md`](file:///Users/honey0025/fuzzwork/results/REPORT.md):
  1. `af670599ce12`: `ASOF Join` UBSan null pointer crash (`asof_issue_26300.sql` / PR #26301)
  2. `246fad5901cf` & `14e4ae09c57e`: `MERGE INTO` rowid internal assertion failure (`merge_pr_26715.sql` / PR #26715)

`REPORT.md` now cleanly displays only genuine defects along with their minimal SQL reproducers and side-by-side build statuses.
