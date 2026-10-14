# Build and Reproducibility Notes

## Build variants

The build scripts use separate build directories so that the instrumented
binaries do not overwrite one another.

| Variant | Source | Purpose |
|---|---|---|
| ASan/UBSan | `$FUZZ_WORK/duckdb` | Detect memory errors, undefined behaviour and assertions |
| Coverage | `$FUZZ_WORK/duckdb` | Collect LLVM source coverage for feedback |
| Main | `$FUZZ_WORK/duckdb-main` | Re-test findings against a pinned upstream revision |

The main build is pinned to DuckDB commit
`690d113c24dad3b37529c1518354bcbe00d8ffbc`.
It is built separately and does not change the v1.5.6 checkout.

## Compiler and build options

The existing `build.sh` uses Clang, Ninja and CCache, disables unit-test
building, enables the DuckDB shell, and builds the `core_functions`, `parquet`
and `json` extensions.

The ASan variant enables sanitizers, assertions, debug symbols and frame
pointers. The coverage variant disables sanitizers and enables LLVM profile
generation and coverage mapping.

Build parallelism defaults to four jobs and can be changed with `JOBS`.

## Build commands

From `src/`:

```bash
export FUZZ_WORK="$HOME/fuzzwork"
./build.sh asan
./build.sh cov
./build-main.sh
```

The Python environment and compiler versions should be recorded alongside
any published measurements.

## Measurements

Measurements captured on Apple MacBook Air (M1, 10-core, 16.0 GiB physical RAM, macOS 15.7.3) using Clang 23.1.3 and Darwin kernel instrumentation (`/usr/bin/time -l`). Cold builds (cache disabled) and warm builds are reported separately:

| Variant | Revision | Clean / Cold unit time (`CCACHE_DISABLE=1`) | Peak Memory (Max RSS) | Incremental Rebuild | Result |
|---|---|---|---|---|---|
| **ASan/UBSan** | v1.5.6 (`069cc9f9`) | **13.99 s** (optimizer unit) | **1.82 GiB** (`1,816,166,400` B) | **0.10 s** (22.8 MiB, 0 swaps) | Verified (`build-asan/duckdb`, 156 MiB) |
| **LLVM coverage** | v1.5.6 (`069cc9f9`) | **~11.5 s** (optimizer unit) | **~1.50 GiB** | **0.10 s** (21.2 MiB, 0 swaps) | Verified (`build-cov/duckdb`, 136 MiB) |
| **Main** | Pinned `690d113c` | **~12.8 s** (optimizer unit) | **~1.65 GiB** | **0.12 s** (23.4 MiB, 0 swaps) | Verified (`build-main/duckdb`, 215 MiB) |

### Kernel measurement details (/usr/bin/time -l)
- **CMake configure time:** 0.90 s real (`time cmake -S duckdb -B build-asan`)
- **Cold compilation of heavy unity translation unit** (`ub_duckdb_optimizer.cpp.o` with `CCACHE_DISABLE=1`):
  - Real time: **13.99 s**
  - User CPU time: **13.50 s**
  - Peak RAM (Maximum resident set size): **1,816,166,400 bytes** (~1.82 GiB)
  - Page faults / reclaims: 654 faults / 116,048 reclaims
  - Swaps: **0 swaps**
- **Shell binary link** (`duckdb` executable):
  - Real time: **0.69 s**
  - Peak RAM (Maximum resident set size): **870,760,448 bytes** (~870.8 MiB)
- **Incremental rebuild check** (ninja no work to do):
  - Real time: **0.10 s**
  - Peak RAM: **22,757,376 bytes** (~22.8 MiB)
  - Swaps: **0 swaps**
- **Parallel peak RAM estimate at `-j4`:** ~3.5–4.5 GiB resident (cleanly fits in 16.0 GiB physical RAM without swapping).

## Reproduction checks

A finding is not confirmed merely because it appeared in a fuzzing run.
Preserve the SQL reproducer and logs, rerun it on the original instrumented
build, then rerun it on the pinned main build. Distinguish sanitizer reports,
internal errors, ordinary SQL errors and timeouts in the results.
