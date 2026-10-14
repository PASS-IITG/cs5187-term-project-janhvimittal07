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

Build duration was not measured during the original run (builds were performed
on 10 October 2026 in a live session and the wall-clock times were not recorded).
The build script targets approximately 7 GiB peak RAM with `-j4`; actual peak
memory was not measured. These measurements are therefore reported as
unavailable, not estimated.
