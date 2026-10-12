#!/usr/bin/env python3
"""Compare a SQL reproducer on the ASan build and pinned DuckDB main."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def classify(returncode, stdout, stderr, timed_out=False):
    text = (stdout + "\n" + stderr).lower()

    if timed_out:
        return "timeout"
    if "addresssanitizer" in text or "undefinedbehaviorsanitizer" in text:
        return "asan"
    if returncode < 0:
        return "crash"
    if "internal error" in text or "fatal error" in text:
        return "internal_error"
    if "parser error" in text or "syntax error" in text:
        return "parse_error"
    if "binder error" in text or "catalog error" in text:
        return "binder_error"
    if "error:" in text or "exception" in text or returncode != 0:
        return "runtime_error"
    return "ok"


def run_query(binary, sql, timeout):
    start = time.monotonic()

    try:
        result = subprocess.run(
            [str(binary), "-c", sql],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout = result.stdout
        stderr = result.stderr
        returncode = result.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        returncode = None
        timed_out = True

    return {
        "binary": str(binary),
        "returncode": returncode,
        "outcome": classify(
            returncode if returncode is not None else -999,
            stdout,
            stderr,
            timed_out,
        ),
        "timed_out": timed_out,
        "duration_seconds": round(time.monotonic() - start, 3),
        "stdout": stdout,
        "stderr": stderr,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Check whether a SQL finding reproduces on pinned DuckDB main."
    )
    parser.add_argument("sql_file", help="SQL reproducer file")
    parser.add_argument(
        "--asan",
        default=os.path.expanduser("~/fuzzwork/build-asan/duckdb"),
    )
    parser.add_argument(
        "--main",
        default=os.path.expanduser("~/fuzzwork/build-main/duckdb"),
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--out",
        default=os.path.expanduser("~/fuzzwork/results/triage"),
    )
    args = parser.parse_args()

    sql_path = Path(args.sql_file).expanduser().resolve()
    asan = Path(args.asan).expanduser().resolve()
    main_binary = Path(args.main).expanduser().resolve()

    if not sql_path.is_file():
        parser.error(f"SQL file not found: {sql_path}")

    for binary in (asan, main_binary):
        if not binary.is_file() or not os.access(binary, os.X_OK):
            parser.error(f"Executable missing or not executable: {binary}")

    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    sql = sql_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    finding_id = digest[:12]
    out_dir = Path(args.out).expanduser().resolve() / finding_id
    out_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(sql_path, out_dir / "reproducer.sql")
    asan_result = run_query(asan, sql, args.timeout)
    main_result = run_query(main_binary, sql, args.timeout)

    interesting = {"asan", "crash", "internal_error", "timeout"}
    reproduces = (
        asan_result["outcome"] in interesting
        and main_result["outcome"] == asan_result["outcome"]
    )

    record = {
        "finding_id": finding_id,
        "sql_sha256": digest,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "timeout_seconds": args.timeout,
        "asan": asan_result,
        "main": main_result,
        "same_outcome": asan_result["outcome"] == main_result["outcome"],
        "reproduces_on_main": reproduces,
        "note": (
            "Automatic classification is a triage signal, not proof of a bug. "
            "Review logs and reproduce again before reporting."
        ),
    }

    for label, result in (("asan", asan_result), ("main", main_result)):
        (out_dir / f"{label}.stdout.txt").write_text(
            result["stdout"], encoding="utf-8"
        )
        (out_dir / f"{label}.stderr.txt").write_text(
            result["stderr"], encoding="utf-8"
        )

    (out_dir / "meta.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )

    print(json.dumps({
        "finding_id": finding_id,
        "asan_outcome": asan_result["outcome"],
        "main_outcome": main_result["outcome"],
        "reproduces_on_main": reproduces,
        "artifacts": str(out_dir),
    }, indent=2))


if __name__ == "__main__":
    main()
