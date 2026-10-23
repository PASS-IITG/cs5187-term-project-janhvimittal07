#!/usr/bin/env bash
set -euo pipefail

ROOT="${FUZZ_WORK:-$(cd "$(dirname "$0")" && pwd)}"
SRC="$ROOT/duckdb-main"
BUILD="$ROOT/build-main"
COMMIT="${DUCKDB_COMMIT:-690d113c24dad3b37529c1518354bcbe00d8ffbc}"
EXT="core_functions;parquet;json"

export PATH="/opt/homebrew/opt/llvm/bin:$PATH"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
    export PATH="$ROOT/.venv/bin:$PATH"
fi

if ! command -v cmake >/dev/null 2>&1; then
    echo "ERROR: cmake is required" >&2
    exit 1
fi
if ! command -v ninja >/dev/null 2>&1; then
    echo "ERROR: ninja is required" >&2
    exit 1
fi
if ! command -v clang++ >/dev/null 2>&1; then
    echo "ERROR: clang++ is required" >&2
    exit 1
fi

if [[ ! -d "$SRC/.git" ]]; then
    if [[ -e "$SRC" ]]; then
        echo "ERROR: $SRC exists but is not a Git checkout; refusing to overwrite it" >&2
        exit 1
    fi
    git clone https://github.com/duckdb/duckdb.git "$SRC"
fi

if [[ -n "$(git -C "$SRC" status --porcelain)" ]]; then
    echo "ERROR: $SRC has local changes. Preserve or commit them before switching revisions." >&2
    exit 1
fi

git -C "$SRC" fetch origin "$COMMIT"
git -C "$SRC" checkout --detach "$COMMIT"

ACTUAL="$(git -C "$SRC" rev-parse HEAD)"
if [[ "$ACTUAL" != "$COMMIT" ]]; then
    echo "ERROR: expected $COMMIT, got $ACTUAL" >&2
    exit 1
fi

JOBS="${JOBS:-4}"

cmake -S "$SRC" -B "$BUILD" -G Ninja \
    -DCMAKE_C_COMPILER=clang \
    -DCMAKE_CXX_COMPILER=clang++ \
    -DCMAKE_C_COMPILER_LAUNCHER=ccache \
    -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
    -DCMAKE_CXX_STANDARD=17 \
    -DBUILD_UNITTESTS=OFF \
    -DBUILD_SHELL=ON \
    -DBUILD_EXTENSIONS="$EXT" \
    -DENABLE_EXTENSION_AUTOLOADING=0 \
    -DENABLE_EXTENSION_AUTOINSTALL=0 \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DENABLE_SANITIZER=ON \
    -DENABLE_UBSAN=ON \
    -DFORCE_ASSERT=ON \
    -DCMAKE_CXX_FLAGS="-fno-omit-frame-pointer"

cmake --build "$BUILD" -j"$JOBS" --target shell

if [[ ! -x "$BUILD/duckdb" ]]; then
    echo "ERROR: expected executable not found: $BUILD/duckdb" >&2
    exit 1
fi

printf 'DuckDB main revision: %s\n' "$ACTUAL"
printf 'Executable: %s\n' "$BUILD/duckdb"
"$BUILD/duckdb" -c "SELECT 1 + 1 AS result;"
