"""Turn DuckDB sqllogictest files into plain SQL seed scripts.

Each .test file becomes corpus/seeds/<dir>__<name>.sql containing its
statements in order (setup context preserved). Files using extensions,
loops, templating or a custom runner are skipped.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

WORK = Path(os.environ.get("FUZZ_WORK", Path(__file__).resolve().parent.parent))  # duckdb/, build-*/, corpus/, results/
TESTS = WORK / "duckdb" / "test" / "sql"
OUT = WORK / "corpus" / "seeds"

SKIP_FILE = re.compile(r"^(require |load |mode skip|skipif|onlyif|loop |foreach |"
                       r"restart|reconnect|concurrentloop|concurrentforeach|"
                       r"test-env|set seed|unzip|cache )", re.M)
DIRECTIVE = re.compile(r"^(statement|query|require|loop|foreach|endloop|concurrentloop|"
                       r"skipif|onlyif|mode|load|restart|reconnect|hash-threshold|"
                       r"sleep|test-env|set |unzip|cache |# )")
BAD_SQL = re.compile(r"\$\{|__TEST_DIR__|__WORKING_DIRECTORY__|\bpragma\s+enable_verification\b"
                     r"|\bINSTALL\b|\bLOAD\b|\bATTACH\b|\bCOPY\b.*\bTO\b|httpfs|s3://|https?://",
                     re.I | re.S)
MAX_BYTES = 60_000


def parse(text: str) -> list[str]:
    stmts: list[str] = []
    lines = text.splitlines()
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
    for f in sorted(TESTS.rglob("*.test")):
        text = f.read_text(errors="replace")
        if SKIP_FILE.search(text):
            skipped += 1
            continue
        stmts = parse(text)
        if len(stmts) < 2:
            skipped += 1
            continue
        body = "\n".join(stmts) + "\n"
        if len(body) > MAX_BYTES:
            skipped += 1
            continue
        rel = f.relative_to(TESTS).with_suffix("")
        (OUT / ("__".join(rel.parts) + ".sql")).write_text(body)
        kept += 1
        nstmts += len(stmts)
    print(f"kept {kept} files ({nstmts} statements), skipped {skipped}", file=sys.stderr)


if __name__ == "__main__":
    main()
