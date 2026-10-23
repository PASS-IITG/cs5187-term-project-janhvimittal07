"""Keep only the static hints that sit inside code added or changed between two DuckDB releases.

    python static/new_code_hints.py --old v1.5.6 --new v2.0.0 --hints results/hints-v200.json --out results/hints-v200-new.json

Why: a 1.5.6 -> 2.0.0 diff is ~5,000 files / 600k lines, but most of that is data tables (tpcds, tpch, icu) and
generated glue. The bugs upstream is not already covering are in the *new* engine code (e.g. the delim-join-to-CTE
rewrite, triggers, new optimizers), so LLM prompts that target hints inside changed lines spend their budget there.
Hints in files SQL cannot reach (parser glue, tree renderers, I/O queues) are dropped.

The hints come from extract_hints.py run on the NEW tag's source:
    python static/extract_hints.py --out results/hints-v200.json --subsystems src/optimizer src/planner \
        src/execution src/function src/storage src/parser src/catalog src/main src/common extension/core_functions \
        extension/json extension/parquet
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import os
import re
import subprocess
from pathlib import Path

WORK = Path(os.environ.get("FUZZ_WORK", Path(__file__).resolve().parent.parent))

# generated or unreachable-from-SQL code: lots of "hints", no way for a query to reach them
BOILERPLATE = re.compile(
    r"generated|sql_formatter|tree_renderer|async_write_queue|/peg/transformer/|json_serializer|serialize_|enum_util|"
    r"extension/tpc|extension/icu|keyword_helper|_ast\.cpp|logging/|/main/capi/|http|secret|settings/|profiling")


def changed_lines(repo: Path, old: str, new: str, paths: list[str]) -> dict[str, list[tuple[int, int]]]:
    """file -> sorted list of (first, last) line numbers added or modified in `new` relative to `old`."""
    p = subprocess.run(["git", "-C", str(repo), "diff", "-U0", "--no-renames", "--diff-filter=AM", old, new, "--", *paths],
                       capture_output=True, text=True, errors="replace")
    ranges: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    cur = None
    for line in p.stdout.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:].strip()
        elif line.startswith("@@") and cur:
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                a = int(m.group(1))
                n = int(m.group(2)) if m.group(2) is not None else 1
                if n > 0:
                    ranges[cur].append((a, a + n - 1))
    return {f: sorted(r) for f, r in ranges.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", default="v1.5.6")
    ap.add_argument("--new", default="v2.0.0")
    ap.add_argument("--hints", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", default=str(WORK / "duckdb"))
    ap.add_argument("--keep-boilerplate", action="store_true")
    a = ap.parse_args()

    for tag in (a.old, a.new):
        if subprocess.run(["git", "-C", a.repo, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"], capture_output=True).returncode != 0:
            raise SystemExit(f"tag {tag} is missing in {a.repo} (shallow clone?): git -C {a.repo} fetch --depth 1 origin tag {tag}")
    hints = json.loads(Path(a.hints).read_text())
    files = sorted({h["file"] for h in hints})
    idx = changed_lines(Path(a.repo), a.old, a.new, files)

    def is_changed(f: str, line: int) -> bool:
        r = idx.get(f)
        if not r:
            return False
        i = bisect.bisect_right(r, (line, 10 ** 9)) - 1
        return i >= 0 and r[i][0] <= line <= r[i][1]

    kept = [h for h in hints if is_changed(h["file"], h["line"]) and (a.keep_boilerplate or not BOILERPLATE.search(h["file"]))]
    Path(a.out).write_text(json.dumps(kept))
    per_file = collections.Counter(h["file"] for h in kept)
    print(f"{len(hints)} hints -> {len(kept)} inside lines changed {a.old}..{a.new} (boilerplate removed: {not a.keep_boilerplate})")
    print("by subsystem:", collections.Counter("/".join(h["file"].split("/")[:2]) for h in kept).most_common())
    print("top files:", [(Path(f).name, n) for f, n in per_file.most_common(8)])


if __name__ == "__main__":
    main()
