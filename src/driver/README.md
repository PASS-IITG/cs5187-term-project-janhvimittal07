# driver/ — in-process batch driver  (owner: Niket)

Task **N3** (core). `harness/runner.py` starts one DuckDB CLI process per
script, so process start-up dominates. Build a driver that opens DuckDB once
(C API or C++), runs many scripts, isolates crashes (fork per batch or a
supervisor that restarts), enforces a per-query timeout, and reports one
JSON line per script: `{"seed", "outcome", "signature", "secs"}`.

Build it against the same source tree as build-asan/ so ASan works.
Contract: keep `runner.classify()` outcome names (ok, parse_error,
binder_error, runtime_error, internal_error, asan, crash, timeout).
