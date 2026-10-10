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

Do not fill in these values from estimates. Measure them on the machine used
for the experiment and record the operating system, chip, RAM, compiler
version, DuckDB revision, build variant, wall-clock build time and peak
memory. A cold build and an incremental build should be reported separately.

| Variant | Revision | Clean build time | Peak memory | Result |
|---|---|---|---|---|
| ASan/UBSan | v1.5.6 | Not measured here | Not measured here | Previously built locally |
| LLVM coverage | v1.5.6 | Not measured here | Not measured here | Previously built locally |
| Main | Pinned commit above | Pending | Pending | Pending |

## Reproduction checks

A finding is not confirmed merely because it appeared in a fuzzing run.
Preserve the SQL reproducer and logs, rerun it on the original instrumented
build, then rerun it on the pinned main build. Distinguish sanitizer reports,
internal errors, ordinary SQL errors and timeouts in the results.
