"""Extract boundary constants, comparisons, and risky sinks from DuckDB source.

Output format adheres strictly to docs/WORKPLAN.md §5:
[
  {
    "file": "src/execution/...cpp",
    "line": 123,
    "kind": "magic_const|branch|sink",
    "value": "2048",
    "function": "Foo::Bar",
    "context": "<5 lines of source>"
  },
  ...
]
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
import time
from pathlib import Path

import tree_sitter_cpp as tscpp
from tree_sitter import Language, Parser, Query, QueryCursor

CPP_LANG = Language(tscpp.language())

CODE = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("FUZZ_WORK", Path(__file__).resolve().parent.parent))  # duckdb/, build-*/, corpus/, results/
SRC = WORK / "duckdb"
DEFAULT_OUT = Path(__file__).resolve().parent / "hints.json"
DEFAULT_SUBSYSTEMS = ["src/optimizer", "src/planner", "src/execution", "src/function", "src/storage"]

# Boundary values, powers of 2, vector limits, and integer boundary markers
BOUNDARY_NUMS = {
    0, 1, 2, 4, 8, 16, 32, 64, 128, 255, 256, 512, 1023, 1024, 1025,
    2047, 2048, 2049, 4096, 8192, 16384, 32768, 65535, 65536,
    2147483647, -2147483648, 2147483648, 9223372036854775807, -9223372036854775808,
}
HEX_PAT = re.compile(r"^0x[0-9a-fA-F]+$")

COMPARISON_OPS = {"<", "<=", "==", "!=", ">", ">="}
SINK_CALLS = {
    "D_ASSERT", "assert", "ASSERT_RESTRICT", "memcpy", "memmove", "memset",
    "NumericCast", "LossyNumericCast", "UnsafeNumericCast",
}

QUERY_STR = """
(number_literal) @num
(binary_expression) @bin
(call_expression) @call
"""

FUNC_PAT = re.compile(
    r"^\s*(?:static\s+|inline\s+|virtual\s+)?"
    r"(?:[a-zA-Z0-9_<>,:*\s&]+?\s+)?"
    r"([a-zA-Z0-9_]+(?:::[a-zA-Z0-9_~]+)?)\s*\([^{;]*\)\s*"
    r"(?:const\s*)?(?:override\s*)?(?:->\s*[^;{]+)?\s*\{?$"
)
EXCLUDE_KW = {"if", "for", "while", "switch", "catch", "return", "sizeof", "decltype"}


def get_context(lines: list[str], line_1idx: int, radius: int = 2) -> str:
    idx = line_1idx - 1
    start = max(0, idx - radius)
    end = min(len(lines), idx + radius + 1)
    return "\n".join(lines[start:end])


def build_function_map(lines: list[str]) -> list[tuple[int, str]]:
    fn_map: list[tuple[int, str]] = []
    for lidx, line in enumerate(lines, 1):
        m = FUNC_PAT.match(line)
        if m:
            name = m.group(1)
            if name not in EXCLUDE_KW:
                fn_map.append((lidx, name))
    return fn_map


def lookup_function(fn_map: list[tuple[int, str]], line: int) -> str:
    curr = "global"
    for sl, name in fn_map:
        if sl <= line:
            curr = name
        else:
            break
    return curr


def extract_from_file(
    file_path: Path, src_root: Path, parser: Parser, query: Query
) -> list[dict]:
    rel_path = str(file_path.resolve().relative_to(src_root))
    code_bytes = file_path.read_bytes()
    code_lines = code_bytes.decode("utf-8", errors="replace").splitlines()
    tree = parser.parse(code_bytes)

    line_offsets = [0] + [idx + 1 for idx, b in enumerate(code_bytes) if b == 10]
    fn_map = build_function_map(code_lines)
    qc = QueryCursor(query)
    captures = qc.captures(tree.root_node)

    hints: list[dict] = []
    seen: set[tuple[int, str, str]] = set()

    def add_hint(sb: int, kind: str, value: str):
        line = bisect.bisect_right(line_offsets, sb)
        key = (line, kind, value)
        if key in seen:
            return
        seen.add(key)
        hints.append({
            "file": rel_path,
            "line": line,
            "kind": kind,
            "value": value,
            "function": lookup_function(fn_map, line),
            "context": get_context(code_lines, line),
        })

    # 1. magic_const
    for node in captures.get("num", []):
        sb, eb = node.start_byte, node.end_byte
        if sb < 0 or eb > len(code_bytes):
            continue
        val_str = code_bytes[sb:eb].decode("utf-8", errors="replace").rstrip("uUlLfF")
        try:
            num_val = int(val_str, 0)
            is_boundary = num_val in BOUNDARY_NUMS or bool(HEX_PAT.match(val_str)) or (num_val > 0 and (num_val & (num_val - 1)) == 0)
        except ValueError:
            is_boundary = bool(HEX_PAT.match(val_str))

        if is_boundary:
            add_hint(sb, "magic_const", val_str)

    # 2. branch (comparison expressions)
    for node in captures.get("bin", []):
        sb, eb = node.start_byte, node.end_byte
        if sb < 0 or eb > len(code_bytes):
            continue
        raw = code_bytes[sb:eb].decode("utf-8", errors="replace")
        clean = " ".join(raw.split())
        words = set(clean.split())
        if any(op in words for op in COMPARISON_OPS) and len(clean) <= 120:
            add_hint(sb, "branch", clean)

    # 3. sink (assertions, memory copies, risky size casts)
    for node in captures.get("call", []):
        sb, eb = node.start_byte, node.end_byte
        if sb < 0 or eb > len(code_bytes):
            continue
        raw = code_bytes[sb:eb].decode("utf-8", errors="replace")
        fn_call = raw.split("(")[0].strip()
        base_fn = fn_call.split("::")[-1].strip().split("->")[-1].split(".")[-1]
        if base_fn in SINK_CALLS:
            call_clean = " ".join(raw.split())[:120]
            add_hint(sb, "sink", call_clean)

    return hints


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Output path for hints.json")
    ap.add_argument("--src", default=str(SRC), help="DuckDB root directory")
    ap.add_argument("--subsystems", nargs="*", default=DEFAULT_SUBSYSTEMS, help="Subsystems to scan")
    ap.add_argument("--limit", type=int, default=0, help="Limit number of files scanned (for testing)")
    a = ap.parse_args()

    src_root = Path(a.src)
    if not src_root.exists():
        print(f"Error: {src_root} not found. Run setup.sh first.", file=sys.stderr)
        sys.exit(1)

    out_file = Path(a.out)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    parser = Parser(CPP_LANG)
    query = Query(CPP_LANG, QUERY_STR)

    all_hints: list[dict] = []
    kind_counts = {"magic_const": 0, "branch": 0, "sink": 0}

    files: list[Path] = []
    for sub in a.subsystems:
        sub_dir = src_root / sub
        if sub_dir.exists():
            files.extend(sorted(sub_dir.rglob("*.cpp")))

    if a.limit > 0:
        files = files[:a.limit]

    print(f"Scanning {len(files)} C++ source files across {len(a.subsystems)} subsystems...", file=sys.stderr)
    t0 = time.time()
    for f in files:
        try:
            file_hints = extract_from_file(f, src_root, parser, query)
            for h in file_hints:
                kind_counts[h["kind"]] += 1
            all_hints.extend(file_hints)
        except Exception as e:
            print(f"Error parsing {f}: {e}", file=sys.stderr)

    # Sort deterministically by file and line
    all_hints.sort(key=lambda h: (h["file"], h["line"], h["kind"]))

    out_file.write_text(json.dumps(all_hints, indent=2))
    elapsed = time.time() - t0
    size_mb = out_file.stat().st_size / (1024 * 1024)

    mc = kind_counts["magic_const"]
    br = kind_counts["branch"]
    sk = kind_counts["sink"]
    print(f"\nExtraction complete in {elapsed:.2f}s:")
    print(f"  Total hints extracted: {len(all_hints):,}")
    print(f"  - magic_const:        {mc:,}")
    print(f"  - branch:             {br:,}")
    print(f"  - sink:               {sk:,}")
    print(f"  Output saved to:      {out_file} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
