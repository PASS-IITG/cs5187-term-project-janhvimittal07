"""Mine closed DuckDB issues for crash / INTERNAL-error reproducers (SmartFuzz's "crash-reuse" idea, no LLM).

    python harness/mine_issues.py --since 2025-06-01

Fixed bugs mark code that was fragile; variants of them (other types, other window frames, other operators) and
incomplete fixes are the most likely place for a new bug on the next release. Output:
  corpus/seeds-issues/issue-<N>-<k>.sql   one SQL block per file (seed corpus for mutate_corpus.py)
  results/issues-replay.jsonl             {"id", "sql"} lines for asan_replay.py, each in a plain and an
                                          enable_verification variant

Issue text is untrusted. Only text inside code blocks is used, never executed as anything but SQL on the sandboxed
ASan build, and any statement that touches files, the network, extensions or settings is dropped; every replayed script
also starts with SET enable_external_access=false.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402

WORK = runner.WORK
QUERIES = ["INTERNAL Error", "AddressSanitizer", "Segmentation fault", "heap-buffer-overflow", "Assertion failed",
           "UndefinedBehaviorSanitizer", "signed integer overflow", "FATAL Error", "use-after-free", "crash"]
BLOCK = re.compile(r"```(?:sql|SQL|duckdb)?\s*\n(.*?)```", re.S)
SQLISH = re.compile(r"^\s*(select|with|create|insert|from|values|update|delete|merge|pragma|set|explain|drop|alter|"
                    r"prepare|execute|install|load|copy|attach|call|summarize|describe|show|use|begin|commit|table)\b", re.I | re.M)
UNSAFE = re.compile(r"\b(copy|export|import|attach|detach|install|load|force|checkpoint|read_\w+|write_\w+|glob|"
                    r"sniff_csv|parquet_\w+|http\w*|s3|httpfs|shell|system|getenv|current_setting|"
                    r"enable_external_access|allow_\w+|extension_directory|temp_directory|secret|sleep)\b"
                    r"|['\"](https?|s3|file|/|\.\./|~)", re.I)
SKIP_LINE = re.compile(r"^\s*(\.|\$|>>>|duckdb>|D\s)", re.M)


def search(query: str, since: str) -> list[dict]:
    p = subprocess.run(["gh", "search", "issues", "--repo", "duckdb/duckdb", query, "--state", "closed", "--limit", "300",
                        "--json", "number,title,body,createdAt"], capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        print(f"  search failed for {query!r}: {p.stderr[:120]}", file=sys.stderr)
        return []
    return [i for i in json.loads(p.stdout) if i["createdAt"][:10] >= since]


def clean(block: str) -> str | None:
    """Keep the safe statements of one block; None if nothing usable is left."""
    if not SQLISH.search(block) or SKIP_LINE.search(block):
        return None
    keep = []
    for stmt in runner.split_statements(block):
        s = stmt.strip()
        if not s or UNSAFE.search(s) or len(s) > 4000:
            continue
        keep.append(s.rstrip(";") + ";")
    return "\n".join(keep) if keep else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2025-06-01")
    a = ap.parse_args()

    issues: dict[int, dict] = {}
    for q in QUERIES:
        got = search(q, a.since)
        print(f"{q!r}: {len(got)} issues since {a.since}", flush=True)
        for i in got:
            issues.setdefault(i["number"], i)
    seeds_dir = WORK / "corpus" / "seeds-issues"
    seeds_dir.mkdir(parents=True, exist_ok=True)
    out = WORK / "results" / "issues-replay.jsonl"
    n_files = n_lines = 0
    with open(out, "w") as fh:
        for num, i in sorted(issues.items()):
            for k, m in enumerate(BLOCK.finditer(i["body"] or "")):
                sql = clean(m.group(1))
                if not sql:
                    continue
                (seeds_dir / f"issue-{num}-{k}.sql").write_text(sql + "\n")
                n_files += 1
                for tag, pre in (("plain", ""), ("verify", "PRAGMA enable_verification;\n")):
                    fh.write(json.dumps({"id": f"issue-{num}-{k}:{tag}", "title": i["title"][:100],
                                         "sql": "SET enable_external_access=false;\n" + pre + sql + "\n"}) + "\n")
                    n_lines += 1
    print(f"{len(issues)} issues -> {n_files} SQL blocks -> {n_lines} replay scripts ({out})", flush=True)


if __name__ == "__main__":
    main()
