"""Sanity checks for the harness; run with .venv/bin/python harness/smoke_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import oracle
import runner

# --- classifier (no build needed) ---
assert runner.classify(0, "", "Parser Error: syntax error at or near x").outcome == "parse_error"
assert runner.classify(0, "", "Binder Error: column z not found").outcome == "binder_error"
assert runner.classify(0, "", "INTERNAL Error: Attempted to access index 5 within vector of size 3").outcome == "internal_error"
assert runner.classify(-11, "", "").outcome == "crash"
assert runner.classify(1, "", "==1==ERROR: AddressSanitizer: heap-buffer-overflow\n    #0 0x1 in foo(int)").outcome == "asan"
assert runner.split_statements("select 'a;b'; select 2;") == ["select 'a;b';", "select 2;"]
print("classifier ok")

# --- oracle (python duckdb) ---
setup = ["CREATE TABLE t AS SELECT i, i % 7 AS g, CASE WHEN i % 5 = 0 THEN NULL ELSE i END AS n FROM range(5000) r(i)"]
d = oracle.check("SELECT g, count(*), sum(n) FROM t GROUP BY g", setup)
print("diff:", "MISMATCH" if d.mismatch else "agree")
t = oracle.check_tlp("SELECT * FROM t", "n > 100", setup)
print("tlp:", "MISMATCH" if t.mismatch else "agree")

# --- CLI runner, if a build exists ---
for b in ("asan", "cov"):
    if runner.BUILDS[b].exists():
        r = runner.run_sql("SELECT 42; SELECT nope FROM nothing;", b)
        print(b, "->", r.outcome, r.errors)
    else:
        print(b, "not built yet")
