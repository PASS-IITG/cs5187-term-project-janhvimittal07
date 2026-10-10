"""Extract boundary constants, comparisons and risky sinks from DuckDB's C++.

Owner: Jahnvi.  Tasks: J1, J2.  Output schema: static/hints.schema.json

    python static/extract_hints.py --src $FUZZ_WORK/duckdb --out static/hints.json

TODO (J1): decide the approach.
  * CodeQL: build a database (heavy; may need >8 GB RAM) and run queries/*.ql
  * fallback: tree-sitter-cpp or `clang -Xclang -ast-dump=json` + a script
TODO (J2): emit one hint per interesting site in the directories listed in
  static/README.md. Keep `context` short (about 5 lines).
"""
from __future__ import annotations

import argparse
import json
import sys

DIRS = ["src/optimizer", "src/planner", "src/execution", "src/function", "src/storage"]


def extract(src_root: str) -> list[dict]:
    raise NotImplementedError("J2: implement hint extraction")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default="static/hints.json")
    a = ap.parse_args()
    hints = extract(a.src)
    json.dump(hints, open(a.out, "w"), indent=1)
    print(f"{len(hints)} hints -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
