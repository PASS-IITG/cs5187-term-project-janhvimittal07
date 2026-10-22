# Week 2 Deliverable: Task N3 — In-Process Batch Driver & Crash Isolation

**Author:** Niket (Execution Engine, Build Sanitizers, Baselines)  
**Date:** October 17, 2026  
**Component:** `src/driver/` (`driver.cpp`, `Makefile`, `test_driver.py`) & `src/harness/runner.py`  
**Git Status:** Ready for review and commit  

---

## 1. Executive Summary & Objective

In fuzzing DuckDB, invoking a new CLI process (`duckdb :memory: -init /dev/null`) for every single query introduces severe operating system overhead:
- Dynamic library loading and ASan/UBSan shadow memory re-initialization
- DuckDB thread pool and catalog initialization/teardown
- Kernel process fork and exec overhead (~100 ms to 150 ms per invocation)

Under AddressSanitizer and UndefinedBehaviorSanitizer instrumentation, standard CLI throughput hovers around **7 to 10 queries per second (QPS)**. Running a 50,000-query campaign using the standard CLI takes ~1.5 to 2 hours of pure process spawn latency.

**Task N3 Deliverable:**
A dedicated C++ in-process batch driver (`src/driver/driver.cpp`) that:
1. Executes thousands of queries in-process, bypassing process creation and catalog spin-up.
2. Provides **strict per-query timeouts** using an asynchronous in-process watchdog thread (`con->Interrupt()`), with supervisor `SIGKILL` fallback for kernel-level stalls.
3. Provides **catalog isolation** via zero-overhead attached temporary in-memory databases (`ATTACH ':memory:' AS fuzz_tmp; ... DETACH fuzz_tmp;`), ensuring queries never pollute each other.
4. Provides **complete crash isolation** via a persistent Supervisor/Worker process architecture: if a query triggers an invalid memory access (`SIGSEGV`) or an ASan/UBSan violation (`SIGABRT`), the supervisor captures the backtrace, attributes the crash to that exact query ID, and automatically respawns a fresh worker to continue batch execution without human intervention.
5. Achieves a **57x to 87x throughput speedup** in isolated mode (**~550 to 840+ QPS** vs 9.6 QPS for CLI).

---

## 2. Driver Architecture

```
                       ┌────────────────────────────────────────────────────────┐
                       │                   SUPERVISOR PROCESS                   │
                       │  - Reads batch JSONL/SQL stream                        │
                       │  - Polls worker pipe with deadline (timeout + 2000 ms) │
                       │  - Catches crash signals (SIGSEGV, SIGABRT)            │
                       │  - Drains ASan/UBSan report from stderr pipe           │
                       │  - Transparently restarts worker on crash              │
                       └───────────────────┬────────────────────────────────────┘
                                           │
                        Piped stdin/stdout │ Non-blocking stderr
                                           ▼
                       ┌────────────────────────────────────────────────────────┐
                       │                     WORKER PROCESS                     │
                       │                                                        │
                       │  ┌──────────────────────────────────────────────────┐  │
                       │  │ Watchdog Thread:                                 │  │
                       │  │   cv.wait_for(timeout_ms)                        │  │
                       │  │   If expired -> con->Interrupt()                 │  │
                       │  └──────────────────────────────────────────────────┘  │
                       │                                                        │
                       │  ┌──────────────────────────────────────────────────┐  │
                       │  │ Catalog Isolation (RAII Scope):                  │  │
                       │  │   ATTACH ':memory:' AS fuzz_isolate;             │  │
                       │  │   USE fuzz_isolate;                              │  │
                       │  │   ... execute statements in query ...            │  │
                       │  │   USE memory; DETACH fuzz_isolate;               │  │
                       │  └──────────────────────────────────────────────────┘  │
                       │                                                        │
                       │  DuckDB C++ Core Engine (In-Memory, ASan/UBSan/Cov)    │
                       └────────────────────────────────────────────────────────┘
```

### Key Technical Mechanisms

1. **Watchdog Interruption (`con->Interrupt()`)**:
   - Each query is monitored by a companion watchdog thread sleeping on a condition variable for `timeout_ms`.
   - When a query completes normally (which takes < 5 ms for 99% of fuzz queries), the main thread signals the condition variable, immediately waking and joining the watchdog with zero wasted latency.
   - If a query enters an infinite loop, the condition variable expires and invokes DuckDB's native interrupt API (`con->Interrupt()`). DuckDB terminates query execution and returns `INTERRUPT Error: Interrupted!`.

2. **Catalog Isolation via RAII Attach/Detach**:
   - In naïve in-process execution, a query creating table `t` causes subsequent queries to fail with `Table with name "t" already exists`.
   - The driver wraps each script execution in a `CatalogIsolation` RAII scope:
     ```cpp
     con.Query("ATTACH ':memory:' AS fuzz_isolate;");
     con.Query("USE fuzz_isolate;");
     // Execute batch statements...
     con.Query("USE memory;");
     con.Query("DETACH fuzz_isolate;");
     ```
   - All tables, views, and temporary catalogs created by the query are completely wiped out upon detachment within ~1 millisecond.

3. **Crash & Sanitizer Isolation**:
   - If a query triggers an invalid pointer dereference or a sanitizer violation:
     - ASan/UBSan aborts the child worker process (`SIGABRT`, exit code 134).
     - The supervisor detects worker death via `poll()` EOF and `waitpid()`.
     - The supervisor drains the worker's stderr pipe to capture the full AddressSanitizer/UndefinedBehaviorSanitizer report.
     - The supervisor outputs the finding as `outcome: "asan"` (or `"crash"`) with the backtrace, then automatically spawns a fresh worker child and immediately delivers the next query in the queue.

---

## 3. Build & Invocation

Located in `src/driver/`:
- `driver.cpp`: C++ driver source
- `Makefile`: Multi-target build script
- `test_driver.py`: Automated test suite and QPS benchmark

### Compilation Targets

```bash
# Build ASan driver (linked against $FUZZ_WORK/build-asan)
make -C src/driver asan

# Build Coverage driver (linked against $FUZZ_WORK/build-cov)
make -C src/driver cov

# Run automated tests and benchmark
make -C src/driver test
```

### CLI Options

```text
Usage: driver [options] [script.sql]
Options:
  --batch <file.jsonl>  Execute queries from JSONL file
  --sql <file.sql>      Execute single SQL file
  --timeout <seconds>   Per-query timeout (default 30.0)
  --db <path>           Target database (default :memory:)
  --isolate             Catalog isolation via attach/detach (default: on)
  --no-isolate          Disable catalog isolation (fastest, shared state)
  --batch-size <N>      Recycle worker process after N queries (default 1000)
  --capture-stdout      Capture tabular result stdout
  --worker              Run directly in worker mode (no supervisor)
```

---

## 4. Empirical Validation & Benchmarks

Kernel and test measurements conducted on Apple Silicon (M-series, ARM64) with AddressSanitizer and UndefinedBehaviorSanitizer enabled:

### Benchmark A: Query Throughput Comparison (QPS)

| Runner Configuration | Queries | Total Time | Throughput (QPS) | Relative Speedup |
|---|---|---|---|---|
| **Standard CLI Process (`duckdb :memory:`)** | 20 | 2.074 s | **9.6 QPS** | 1.0x (Baseline) |
| **In-Process Driver (Isolated Catalog)** | 200 | 0.363 s | **550.8 QPS** | **57.1x speedup** |
| **In-Process Driver (Shared Catalog)** | 200 | 0.239 s | **838.1 QPS** | **86.9x speedup** |

*Takeaway:* Running 10,000 queries via the standard CLI would take ~17.3 minutes. The in-process driver executes the same 10,000 queries in **18.1 seconds**.

---

### Benchmark B: Crash Isolation on Known Upstream Bugs

Tested against the two verified upstream bugs from Milestone 1:

#### 1. ASOF Join Null-Pointer Bug (`asof_issue_26300.sql` / PR #26301)
- **Execution behavior:** Child worker triggers UBSan null pointer violation at `table_scan.cpp:819:33` and raises `SIGABRT` (`returncode = -6`).
- **Supervisor response:** Supervisor catches worker exit, extracts `SUMMARY: UndefinedBehaviorSanitizer: undefined-behavior table_scan.cpp:819:33`, emits `outcome: "asan"`, spawns a fresh worker in 1.7 ms, and executes the next query without failure.

#### 2. MERGE INTO RowID Binding Bug (`merge_pr_26715.sql` / PR #26715)
- **Execution behavior:** DuckDB raises assertion failure `INTERNAL Error: Failed to bind column reference "rowid"`.
- **Driver response:** Driver catches the internal exception, formats the full DuckDB stack trace, sets `outcome: "internal_error"`, and continues execution in-process.

---

## 5. Python Harness Integration

`src/harness/runner.py` has been updated with:
1. `DRIVERS`: Mapping to compiled driver executables (`driver-asan`, `driver-cov`).
2. `run_batch(items, build="asan", timeout=30.0, isolate=True, capture_stdout=False)`: Sends query batches directly to the batch driver, automatically falling back to CLI execution if the binary is missing.
3. Accelerated `find_culprit(setup, stmts)`: Uses `run_batch()` to evaluate all prefix statements in parallel/in-process, shrinking delta-debugging times from seconds to tens of milliseconds.
