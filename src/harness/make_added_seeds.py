"""Build the seed set of tests ADDED between two releases (the new-feature seeds).

    python harness/make_added_seeds.py --old v1.5.6 --new v2.0.0

1. extract_seeds.py on the current checkout (must be at --new) -> corpus/seeds-<new>/
2. `git diff --name-status <old> <new> -- test/sql`, keep status A (added) *.test files that survived extraction
   -> corpus/seeds-<new>-added/ (file-writing directories copy/export/attach/storage are set aside)

Use git, not a comparison against an older corpus: seed counts differ between releases for reasons unrelated to new
features (extractor filters, mass test reformatting: ~3,100 test files were merely *modified* between 1.5.6 and 2.0.0).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import extract_seeds  # noqa: E402

WORK = extract_seeds.WORK
IO_DIRS = {"copy", "sql_export", "export", "attach", "storage"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", default="v1.5.6")
    ap.add_argument("--new", default="v2.0.0")
    a = ap.parse_args()
    src = WORK / "duckdb"
    cur = subprocess.run(["git", "-C", str(src), "describe", "--tags", "--exact-match"], capture_output=True, text=True).stdout.strip()
    if cur != a.new:
        sys.exit(f"{src} is at {cur!r}, expected {a.new}: git -C {src} checkout {a.new}")

    full = WORK / "corpus" / f"seeds-v{a.new.lstrip('v').replace('.', '')}"
    shutil.rmtree(full, ignore_errors=True)
    extract_seeds.OUT = full
    extract_seeds.main()

    added = WORK / "corpus" / f"{full.name}-added"
    shutil.rmtree(added, ignore_errors=True)
    added.mkdir(parents=True)
    names = subprocess.run(["git", "-C", str(src), "diff", "--name-status", a.old, a.new, "--", "test/sql"],
                           capture_output=True, text=True).stdout.splitlines()
    kept = io = 0
    for line in names:
        status, _, path = line.partition("\t")
        p = Path(path.strip())
        if status != "A" or p.suffix != ".test":
            continue
        rel = p.relative_to("test/sql").with_suffix("")
        f = full / ("__".join(rel.parts) + ".sql")
        if not f.exists():
            continue
        if rel.parts[0] in IO_DIRS:
            io += 1
            continue
        shutil.copy(f, added / f.name)
        kept += 1
    print(f"added tests kept: {kept} -> {added}   (file-writing directories set aside: {io})")


if __name__ == "__main__":
    main()
