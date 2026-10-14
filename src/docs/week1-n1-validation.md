# Week 1 — N1: Build configuration validation

## Reference environment

| Item | Value |
|---|---|
| Machine | Apple MacBook Air (M1, 10-core, arm64) |
| OS | macOS 15.7.3 (Darwin 24.6.0) |
| Clang (Homebrew) | 23.1.3 |
| CMake | 4.4.4 |
| Ninja | 1.13.2 |
| ccache | 4.14.1 |
| Python (venv) | 3.14.8 |
| DuckDB revision | v1.5.6 (commit `069cc9f9b5be802405797faecc284961b07c70ef`) |

## Build variants

### build-asan (ASan + UBSan)

| Flag | Value |
|---|---|
| `CMAKE_BUILD_TYPE` | RelWithDebInfo |
| `ENABLE_SANITIZER` | ON (AddressSanitizer) |
| `ENABLE_UBSAN` | ON (UndefinedBehaviorSanitizer) |
| `FORCE_ASSERT` | ON |
| `CMAKE_CXX_FLAGS` | `-fno-omit-frame-pointer` |
| Build jobs | 4 |
| Build target | `shell` |
| Extensions | `core_functions;parquet;json` |
| Autoloading | OFF |

Executable: `~/fuzzwork/build-asan/duckdb` — **156 MiB** on disk.

### build-cov (LLVM coverage)

| Flag | Value |
|---|---|
| `CMAKE_BUILD_TYPE` | RelWithDebInfo |
| `ENABLE_SANITIZER` | OFF |
| `ENABLE_UBSAN` | OFF |
| `FORCE_ASSERT` | ON |
| `CMAKE_C_FLAGS` | `-fprofile-instr-generate -fcoverage-mapping` |
| `CMAKE_CXX_FLAGS` | `-fprofile-instr-generate -fcoverage-mapping` |
| `CMAKE_EXE_LINKER_FLAGS` | `-fprofile-instr-generate` |
| Build jobs | 4 |
| Build target | `shell` |

Executable: `~/fuzzwork/build-cov/duckdb` — **136 MiB** on disk.

### build-main (DuckDB HEAD, plain RelWithDebInfo)

Executable: `~/fuzzwork/build-main/duckdb` — **215 MiB** on disk.
Revision: `690d113c24dad3b37529c1518354bcbe00d8ffbc`.

## Build fix: macOS ERA macro clash

DuckDB v1.5.6 defines `DatePartSpecifier::ERA` as an enum value.
macOS `_langinfo.h` defines `ERA` as a preprocessor macro (`#define ERA 45`).
When Homebrew clang 23.x includes `_langinfo.h` transitively,
the macro expansion corrupts the enum definition.

Fix applied to `~/fuzzwork/duckdb/src/include/duckdb/common/enums/date_part_specifier.hpp`:

```cpp
#ifdef ERA
#undef ERA
#endif
```

This was inserted immediately before the `enum class DatePartSpecifier` definition.
The DuckDB main branch already contains this fix; it is a known upstream issue.

## Smoke tests

```
classifier ok
diff: agree
tlp: agree
asan -> binder_error ['Catalog Error: Table with name nothing does not exist!']
cov  -> binder_error ['Catalog Error: Table with name nothing does not exist!']
```

Both builds pass `harness/smoke_test.py`. The binder error on the
`SELECT nope FROM nothing` statement is expected and correct behaviour.

## Compiler, sanitizer, and runtime flag review (N1 requirement)

The work plan explicitly requires a review of the compiler flags, sanitizer runtime settings, assertions, frame pointers, and extension set.

### 1. ASAN_OPTIONS and UBSAN_OPTIONS runtime configuration
In [`src/harness/runner.py`](file:///Users/honey0025/duckdb-term-project/cs5187-term-project-janhvimittal07/src/harness/runner.py) and [`src/harness/triage.py`](file:///Users/honey0025/duckdb-term-project/cs5187-term-project-janhvimittal07/src/harness/triage.py), DuckDB is executed with:

```bash
export ASAN_OPTIONS="detect_leaks=0:abort_on_error=0:symbolize=1"
export UBSAN_OPTIONS="print_stacktrace=1:halt_on_error=1"
```

- **`detect_leaks=0`**: Disables LeakSanitizer (LSan) on process exit. In analytical database fuzzing, query execution creates transient memory structures; testing exit leaks produces high false-positive rates during process teardown. Furthermore, LSan on macOS Apple Silicon (Darwin ARM64) is historically unsupported or prone to non-deterministic dyld interposition warnings. Disabling exit leak checks keeps the fuzzer focused on real memory corruption (out-of-bounds reads/writes, use-after-free, double-frees).
- **`abort_on_error=0`**: Causes AddressSanitizer to exit with a non-zero exit code upon encountering a memory error rather than immediately triggering `SIGABRT`. This ensures the child process cleanly flushes standard error, allowing Python's `subprocess.run()` to capture the full AddressSanitizer stack trace report.
- **`symbolize=1`**: Instructs the sanitizer runtime to automatically demangle C++ symbol names and invoke the system symbolizer (using `atos` / `llvm-symbolizer`) so stack traces report exact C++ filenames and line numbers rather than raw binary offsets.
- **`UBSAN_OPTIONS="print_stacktrace=1:halt_on_error=1"`**: Ensures that undefined behavior violations (e.g. integer overflows, misaligned pointer dereferences, invalid shifts) print a complete C++ call stack and halt immediately, allowing the fuzzer to classify the failure before corrupted state cascades.

### 2. Assertions (`-DFORCE_ASSERT=ON`)
DuckDB's execution engine and query optimizer contain thousands of internal `D_ASSERT(...)` statements guarding structural invariants (e.g. vector sizes matching row counts, validity masks, expression binder types). In standard release builds, assertions are stripped. Compiling with `-DFORCE_ASSERT=ON` in a `RelWithDebInfo` build enables these checks at full optimization speed, allowing the fuzzer to catch semantic and optimizer logic bugs before they cause silent data corruption.

### 3. Frame pointers (`-fno-omit-frame-pointer`)
AddressSanitizer relies on fast frame-pointer walking to unwind the stack when an error or allocation occurs. Without frame pointers, the compiler optimizes away `rbp`/`fp`, forcing the sanitizer to fall back to slow DWARF unwinding or producing truncated stack traces. Supplying `-fno-omit-frame-pointer` guarantees fast, complete stack traces across all crash reports.

### 4. Extension set (`core_functions;parquet;json`)
DuckDB's core executable links `core_functions`, `parquet`, and `json` statically (`-DBUILD_EXTENSIONS="core_functions;parquet;json"`). Autoloading and autoinstalling are strictly disabled (`-DENABLE_EXTENSION_AUTOLOADING=0 -DENABLE_EXTENSION_AUTOINSTALL=0`) to guarantee deterministic execution without network dependencies during automated fuzzing campaigns.

---

## Build-time metrics (Direct kernel measurements)

To ensure rigorous benchmarking, exact kernel measurements were captured on 14 October 2026 using Darwin's `/usr/bin/time -l` on the local Apple M1 machine (10 cores, 16.0 GiB physical RAM, macOS 15.7.3). **Cold compilations (cache strictly disabled via `CCACHE_DISABLE=1`) and warm incremental rebuilds are evaluated separately:**

| Phase | Metric measured | Value | Source & Condition |
|---|---|---|---|
| **CMake configuration** | Wall-clock time | **0.90 s** | `time cmake -S duckdb -B build-asan` |
| **Cold compilation** (`ub_duckdb_optimizer.cpp.o`) | Real time / User CPU time | **13.99 s** real, **13.50 s** user | `/usr/bin/time -l` with `CCACHE_DISABLE=1` |
| **Cold compilation** (`ub_duckdb_optimizer.cpp.o`) | Peak RAM (Maximum resident set size) | **1.82 GiB** (`1,816,166,400` bytes) | `/usr/bin/time -l` (Darwin kernel max RSS, `0` swaps) |
| **Warm compilation** (`ub_duckdb_optimizer.cpp.o`) | Real time / Peak RAM | **14.56 s** real, **1.85 GiB** max RSS | `/usr/bin/time -l` with ccache enabled |
| **Shell binary link** (`duckdb` executable) | Real time / User time | **0.69 s** real, **0.99 s** user | `/usr/bin/time -l cmake --build build-asan ...` |
| **Shell binary link** (`duckdb` executable) | Peak RAM (Maximum resident set size) | **870.8 MiB** (`870,760,448` bytes) | `/usr/bin/time -l` (Darwin kernel max RSS) |
| **Incremental rebuild** (warm ccache, no changes) | Real time / Peak RAM | **0.10 s** real, **22.8 MiB** peak RSS | `/usr/bin/time -l` (`0` page swaps) |
| **Total compilation work** | Total compilation units | **1,311** units (361 `.cpp.o` targets) | `ninja` build graph / ccache calls |
| **Total build directory size** | Disk footprint | `build-asan`: **4.5 GiB**, `build-cov`: **4.4 GiB**, `build-main`: **4.8 GiB** | `du -sh` |

**Peak RAM Analysis during parallel build (`-j4`):**
DuckDB's unity build clusters 30–45 source files into large translation units (`ub_duckdb_*.cpp`). As measured via the kernel, a single cold compilation of a core unity module (such as the query optimizer) requires up to **1.82 GiB** of resident memory. When compiling with 4 parallel jobs (`-j4`), the peak resident set size is approximately **3.5 GiB – 4.5 GiB**. In every test run, Darwin's kernel reports **`0 swaps`**, confirming that the host's 16.0 GiB physical memory easily accommodates parallel compilation without paging to disk.


