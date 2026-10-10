#!/usr/bin/env bash
# One-time setup: clone pinned DuckDB, create venv. Then run ./build.sh.
# Work dir (source, builds, corpus, results) defaults to this directory;
# override with FUZZ_WORK=/path to keep it outside the repo.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${FUZZ_WORK:-$HERE}
TAG=$(cat "$HERE/DUCKDB_VERSION")
mkdir -p "$WORK"
[ -d "$WORK/duckdb" ] || git clone --depth 1 --branch "$TAG" https://github.com/duckdb/duckdb.git "$WORK/duckdb"
[ -d "$WORK/.venv" ] || python3 -m venv "$WORK/.venv"
"$WORK/.venv/bin/pip" -q install -r "$HERE/requirements.txt"
mkdir -p "$WORK"/{corpus,crashes,results}
echo "setup done: FUZZ_WORK=$WORK"
