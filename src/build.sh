#!/usr/bin/env bash
# Builds two DuckDB variants: ASan+UBSan (crash finding) and LLVM coverage (feedback).
# Sequential, -j4, to stay within ~7 GB RAM.
set -euo pipefail
ROOT=${FUZZ_WORK:-$(cd "$(dirname "$0")" && pwd)}
SRC=$ROOT/duckdb
JOBS=${JOBS:-4}
EXT="core_functions;parquet;json"

common=(
  -G Ninja
  -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++
  -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
  -DBUILD_UNITTESTS=OFF -DBUILD_SHELL=ON
  -DBUILD_EXTENSIONS="$EXT"
  -DENABLE_EXTENSION_AUTOLOADING=0 -DENABLE_EXTENSION_AUTOINSTALL=0
)

build_asan() {
  cmake -S "$SRC" -B "$ROOT/build-asan" "${common[@]}" \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DENABLE_SANITIZER=ON -DENABLE_UBSAN=ON -DFORCE_ASSERT=ON \
    -DCMAKE_CXX_FLAGS="-fno-omit-frame-pointer"
  cmake --build "$ROOT/build-asan" -j"$JOBS" --target shell
}

build_cov() {
  cmake -S "$SRC" -B "$ROOT/build-cov" "${common[@]}" \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo -DFORCE_ASSERT=ON \
    -DENABLE_SANITIZER=OFF -DENABLE_UBSAN=OFF \
    -DCMAKE_C_FLAGS="-fprofile-instr-generate -fcoverage-mapping" \
    -DCMAKE_CXX_FLAGS="-fprofile-instr-generate -fcoverage-mapping" \
    -DCMAKE_EXE_LINKER_FLAGS="-fprofile-instr-generate" \
    -DCMAKE_SHARED_LINKER_FLAGS="-fprofile-instr-generate"
  cmake --build "$ROOT/build-cov" -j"$JOBS" --target shell
}

case "${1:-all}" in
  asan) build_asan ;;
  cov)  build_cov ;;
  all)  build_asan; build_cov ;;
esac
echo "BUILD_DONE"
