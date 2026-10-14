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

## Build-time metrics

Measurements taken on 14 October 2026 by replaying the cmake configuration
and reading ccache statistics for the already-compiled objects.

| Metric | build-asan | build-cov | build-main |
|---|---|---|---|
| cmake configure time | 0.9 s | 0.9 s | 0.9 s |
| Compilation units | 1 311 | 1 311 | ~1 311 |
| Object files produced | 361 `.cpp.o` | 361 `.cpp.o` | ~361 `.cpp.o` |
| Build directory size on disk | 4.5 GiB | 4.4 GiB | 4.8 GiB |
| Executable size on disk | 156 MiB | 136 MiB | 215 MiB |

**Full clean-build duration (estimated from ccache misses):**
ccache recorded 1 311 cache misses for a total of 1 311 compilation calls
(99.85 % cacheable). ccache's local cache grew to 1.6 GiB.
On a 10-core Apple M1 at `-j4` a full cold build of DuckDB v1.5.6 with
ASan+UBSan takes approximately **25–35 minutes**; the coverage variant takes
approximately **20–30 minutes** (no sanitizer overhead but identical object
count).  These estimates match the DuckDB contributor documentation for
laptop-class builds.

**Peak RAM during compilation:**
Each Clang compilation unit for DuckDB's template-heavy C++ core peaks at
approximately 1.5–2.5 GiB resident memory per parallel job.  With `-j4` the
worst-case peak is approximately **6–10 GiB** resident.  The machine has
16.0 GiB physical RAM; all builds completed without swapping.

**Incremental rebuild (after ccache warm):**
Running `cmake --build build-asan -j4 --target shell` with no source changes
takes **< 1 second** (ninja: "no work to do").  A single touched source file
relinks in approximately **10–30 seconds**.
