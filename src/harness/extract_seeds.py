"""Turn DuckDB sqllogictest files into plain SQL seed scripts.

Each .test file becomes corpus/seeds/<dir>__<name>.sql containing its
statements in order (setup context preserved). Expands foreach / loop blocks
and ${var} templating while filtering non-portable environment dependencies.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

WORK = Path(os.environ.get("FUZZ_WORK", Path(__file__).resolve().parent.parent))  # duckdb/, build-*/, corpus/, results/
TESTS = WORK / "duckdb" / "test" / "sql"
OUT = WORK / "corpus" / "seeds"

# Files that require external non-portable runners, network extensions, or state resets
SKIP_FILE = re.compile(
    r"^(load |mode skip|skipif|onlyif|"
    r"restart|reconnect|"
    r"test-env|set seed|unzip|cache )",
    re.M | re.I
)

# Supported require directives (in-tree built extensions & test runner flags)
SUPPORTED_REQUIRES = {
    "parquet", "json", "core_functions", "autocomplete", "skip_reload",
    "notwindows", "noforcestorage", "no_alternative_verify",
    "no_extension_autoloading", "block_size", "vector_size",
    "64bit", "no_force_storage", "no_latest_storage", "no_vector_verification"
}
REQUIRE_PAT = re.compile(r"^require\s+(\S+)", re.M | re.I)

DIRECTIVE = re.compile(r"^(statement|query|require|loop|foreach|endloop|concurrentloop|"
                       r"skipif|onlyif|mode|load|restart|reconnect|hash-threshold|"
                       r"sleep|test-env|set |unzip|cache |# )")

BAD_SQL = re.compile(
    r"\$\{|__TEST_DIR__|__WORKING_DIRECTORY__|\bpragma\s+enable_verification\b"
    r"|\bINSTALL\b|\bLOAD\b|\bATTACH\b|\bCOPY\b.*\bTO\b|httpfs|s3://|https?://",
    re.I | re.S
)

MAX_BYTES = 60_000

# sqllogictest token macro definitions matching DuckDB test runner
MACROS = {
    "<numeric>": ["float", "double"],
    "<signed>": ["tinyint", "smallint", "integer", "bigint", "hugeint"],
    "<unsigned>": ["utinyint", "usmallint", "uinteger", "ubigint", "uhugeint"],
    "<integral>": ["tinyint", "smallint", "integer", "bigint", "hugeint",
                   "utinyint", "usmallint", "uinteger", "ubigint", "uhugeint"],
    "<alltypes>": ["tinyint", "smallint", "integer", "bigint", "hugeint",
                   "utinyint", "usmallint", "uinteger", "ubigint", "uhugeint",
                   "float", "double", "bool", "interval", "varchar"],
    "<compression>": ["none", "uncompressed", "rle", "bitpacking", "dictionary", "fsst", "alp", "alprd"]
}


def has_unsupported_require(text: str) -> bool:
    for m in REQUIRE_PAT.finditer(text):
        req = m.group(1).lower()
        if req not in SUPPORTED_REQUIRES:
            return True
    return False


def expand_tokens(params: list[str]) -> list[str]:
    res: list[str] = []
    for p in params:
        p_low = p.lower()
        if p.startswith("!"):
            to_remove = p[1:]
            res = [x for x in res if x != to_remove]
        elif p_low in MACROS:
            res.extend(MACROS[p_low])
        else:
            val = p.strip("\"'")
            if val:
                res.append(val)
    return res


def unroll_blocks(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        stripped = ln.strip()
        if stripped.startswith(("foreach ", "concurrentforeach ")):
            parts = stripped.split()
            if len(parts) >= 2:
                var_name = parts[1]
                tokens = expand_tokens(parts[2:])
                depth = 1
                j = i + 1
                body_lines: list[str] = []
                while j < len(lines):
                    s = lines[j].strip()
                    if s.startswith(("foreach ", "concurrentforeach ", "loop ", "concurrentloop ")):
                        depth += 1
                    elif s.startswith("endloop"):
                        depth -= 1
                        if depth == 0:
                            break
                    body_lines.append(lines[j])
                    j += 1
                unrolled_body = unroll_blocks(body_lines)
                pattern = r"\$\{" + re.escape(var_name) + r"\}"
                for val in tokens:
                    for b_ln in unrolled_body:
                        out.append(re.sub(pattern, val, b_ln))
                i = j + 1
                continue
        elif stripped.startswith(("loop ", "concurrentloop ")):
            parts = stripped.split()
            if len(parts) >= 4:
                var_name = parts[1]
                try:
                    start_idx = int(parts[2])
                    end_idx = int(parts[3])
                except ValueError:
                    start_idx, end_idx = 0, 0
                depth = 1
                j = i + 1
                body_lines = []
                while j < len(lines):
                    s = lines[j].strip()
                    if s.startswith(("foreach ", "concurrentforeach ", "loop ", "concurrentloop ")):
                        depth += 1
                    elif s.startswith("endloop"):
                        depth -= 1
                        if depth == 0:
                            break
                    body_lines.append(lines[j])
                    j += 1
                unrolled_body = unroll_blocks(body_lines)
                pattern = r"\$\{" + re.escape(var_name) + r"\}"
                # Cap loop at max 8 iterations to avoid excessively large scripts
                limit_end = min(end_idx, start_idx + 8)
                for val in range(start_idx, limit_end):
                    for b_ln in unrolled_body:
                        out.append(re.sub(pattern, str(val), b_ln))
                i = j + 1
                continue
        out.append(ln)
        i += 1
    return out


def parse(text: str) -> list[str]:
    stmts: list[str] = []
    lines = unroll_blocks(text.splitlines())
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith(("statement ", "query ")) or ln in ("statement ok", "statement error"):
            i += 1
            buf: list[str] = []
            while i < len(lines) and lines[i].strip() != "" and lines[i].strip() != "----":
                buf.append(lines[i])
                i += 1
            sql = "\n".join(buf).strip()
            if sql and not BAD_SQL.search(sql):
                stmts.append(sql if sql.rstrip().endswith(";") else sql + ";")
            # skip expected-result block
            while i < len(lines) and lines[i].strip() != "":
                i += 1
        else:
            i += 1
    return stmts


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    kept = skipped = nstmts = 0
    reasons = {"skip_file": 0, "unsupported_require": 0, "too_few_stmts": 0, "too_large": 0}

    for f in sorted(TESTS.rglob("*.test")):
        text = f.read_text(errors="replace")
        if SKIP_FILE.search(text):
            skipped += 1
            reasons["skip_file"] += 1
            continue
        if has_unsupported_require(text):
            skipped += 1
            reasons["unsupported_require"] += 1
            continue
        stmts = parse(text)
        if len(stmts) < 2:
            skipped += 1
            reasons["too_few_stmts"] += 1
            continue
        body = "\n".join(stmts) + "\n"
        if len(body) > MAX_BYTES:
            skipped += 1
            reasons["too_large"] += 1
            continue
        rel = f.relative_to(TESTS).with_suffix("")
        (OUT / ("__".join(rel.parts) + ".sql")).write_text(body)
        kept += 1
        nstmts += len(stmts)
    print(f"kept {kept} files ({nstmts} statements), skipped {skipped} ({reasons})", file=sys.stderr)


if __name__ == "__main__":
    main()
