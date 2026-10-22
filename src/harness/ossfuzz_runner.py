"""Google OSS-Fuzz target runner for DuckDB (Task N4).

Builds and runs the native DuckDB libFuzzer harness (`test/ossfuzz/parse_fuzz_test.cpp`)
with ASan or LLVM coverage instrumentation under an equal time budget.

Usage:
    python src/harness/ossfuzz_runner.py --minutes 5 --build asan
    python src/harness/ossfuzz_runner.py --seconds 60 --build cov
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("FUZZ_WORK", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(CODE / "harness"))
sys.path.insert(0, str(CODE / "coverage"))

try:
    import cov  # noqa: E402
except ImportError:
    cov = None

CRASHES = WORK / "crashes"
CORPUS_DIR = WORK / "corpus" / "ossfuzz"
DUCKDB_SRC = WORK / "duckdb"


def compile_ossfuzz_target(build: str = "asan") -> Path:
    """Compile test/ossfuzz/parse_fuzz_test.cpp into duckdb_ossfuzz binary if not present."""
    target_dir = WORK / f"build-{build}"
    bin_path = target_dir / "duckdb_ossfuzz"
    if bin_path.exists():
        return bin_path

    print(f"[*] Compiling duckdb_ossfuzz target for build-{build}...")
    target_dir.mkdir(parents=True, exist_ok=True)
    fuzz_src = DUCKDB_SRC / "test" / "ossfuzz" / "parse_fuzz_test.cpp"
    if not fuzz_src.exists():
        raise FileNotFoundError(f"Missing ossfuzz source at {fuzz_src}")

    # Discover static libraries in target build directory
    lib_dir = target_dir / "src"
    ext_dir = target_dir / "extension"
    tp_dir = target_dir / "third_party"

    static_libs = [
        ext_dir / "libduckdb_generated_extension_loader.a",
        ext_dir / "core_functions" / "libcore_functions_extension.a",
        ext_dir / "parquet" / "libparquet_extension.a",
        ext_dir / "json" / "libjson_extension.a",
        lib_dir / "libduckdb_static.a",
        tp_dir / "libpg_query" / "libduckdb_pg_query.a",
        tp_dir / "re2" / "libduckdb_re2.a",
        tp_dir / "miniz" / "libduckdb_miniz.a",
        tp_dir / "utf8proc" / "libduckdb_utf8proc.a",
        tp_dir / "hyperloglog" / "libduckdb_hyperloglog.a",
        tp_dir / "fastpforlib" / "libduckdb_fastpforlib.a",
        tp_dir / "fsst" / "libduckdb_fsst.a",
        tp_dir / "fmt" / "libduckdb_fmt.a",
        tp_dir / "skiplist" / "libduckdb_skiplistlib.a",
        tp_dir / "mbedtls" / "libduckdb_mbedtls.a",
        tp_dir / "yyjson" / "libduckdb_yyjson.a",
        tp_dir / "zstd" / "libduckdb_zstd.a",
    ]

    missing = [str(l) for l in static_libs if not l.exists()]
    if missing:
        raise FileNotFoundError(f"Missing static libraries required for linking: {missing[:3]}")

    cflags = [
        "clang++", "-O2", "-std=c++17",
        f"-I{DUCKDB_SRC}/src/include",
        str(fuzz_src),
    ]

    if build == "asan":
        cflags.extend(["-fsanitize=address,fuzzer"])
    elif build == "cov":
        cflags.extend(["-fprofile-instr-generate", "-fcoverage-mapping", "-fsanitize=fuzzer"])
    else:
        cflags.extend(["-fsanitize=fuzzer"])

    cflags.extend([str(l) for l in static_libs])
    cflags.extend(["-o", str(bin_path)])

    subprocess.run(cflags, check=True)
    print(f"[+] Successfully compiled: {bin_path}")
    return bin_path


def prepare_seed_corpus(max_inputs: int = 200) -> Path:
    """Prepare initial corpus directory from test/ossfuzz/cases and seeds."""
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    existing = list(CORPUS_DIR.glob("*"))
    if len(existing) >= 20:
        return CORPUS_DIR

    count = 0
    # 1. Historical OSS-Fuzz cases
    cases_dir = DUCKDB_SRC / "test" / "ossfuzz" / "cases"
    if cases_dir.exists():
        for f in cases_dir.glob("clusterfuzz-*"):
            if count >= max_inputs:
                break
            dest = CORPUS_DIR / f"case_{f.name}"
            if not dest.exists():
                shutil.copyfile(f, dest)
                count += 1

    # 2. Add queries from seeds
    seeds_dir = WORK / "corpus" / "seeds"
    if seeds_dir.exists():
        for sf in sorted(seeds_dir.glob("*.sql"))[:50]:
            if count >= max_inputs:
                break
            try:
                content = sf.read_text(errors="replace")
                dest = CORPUS_DIR / f"seed_{sf.name}"
                dest.write_text(content[:1024])
                count += 1
            except Exception:
                pass

    return CORPUS_DIR


def run_ossfuzz(seconds: float = 60.0, build: str = "asan", max_runs: int = 0,
                corpus_dir: Path | None = None) -> dict:
    """Run duckdb_ossfuzz target under specified time budget."""
    bin_path = compile_ossfuzz_target(build=build)
    corpus = corpus_dir or prepare_seed_corpus()
    CRASHES.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(bin_path),
        str(corpus),
        f"-max_total_time={int(seconds)}",
        "-timeout=25",
        "-rss_limit_mb=4096",
        f"-artifact_prefix={CRASHES}/ossfuzz_",
        "-print_final_stats=1",
    ]
    if max_runs > 0:
        cmd.append(f"-runs={max_runs}")

    env = dict(os.environ)
    raw_prof = None
    if build == "cov":
        raw_prof = WORK / "results" / "cov" / "ossfuzz.profraw"
        raw_prof.parent.mkdir(parents=True, exist_ok=True)
        env["LLVM_PROFILE_FILE"] = str(raw_prof)

    print(f"[*] Launching OSS-Fuzz ({build}): budget={seconds}s, corpus={len(list(corpus.glob('*')))} files")
    t0 = time.time()
    proc = subprocess.run(cmd, env=env, text=True, capture_output=True)
    elapsed = round(time.time() - t0, 2)

    # Parse libFuzzer stderr output
    stderr = proc.stderr or ""
    total_execs = 0
    cov_count = 0
    ft_count = 0
    corp_count = 0
    execs_per_sec = 0.0

    # Search for summary lines like:
    # #12345 DONE cov: 1542 ft: 3200 corp: 45/12Kb lim: 4096 exec/s: 250 rss: 300Mb
    for line in stderr.splitlines():
        m = re.search(r"#(\d+)\s+(?:DONE|NEW|pulse)\s+cov:\s+(\d+)\s+ft:\s+(\d+)\s+corp:\s+(\d+).*?exec/s:\s+(\d+)", line)
        if m:
            total_execs = int(m.group(1))
            cov_count = int(m.group(2))
            ft_count = int(m.group(3))
            corp_count = int(m.group(4))
            execs_per_sec = float(m.group(5))

    # Also search final stats if available
    stat_m = re.search(r"stat::number_of_executed_units:\s+(\d+)", stderr)
    if stat_m:
        total_execs = int(stat_m.group(1))

    crashes = len(list(CRASHES.glob("ossfuzz_crash-*")))
    qps = round(total_execs / elapsed, 1) if elapsed > 0 else 0.0

    # If coverage build, merge raw profile into all.profdata
    if build == "cov" and raw_prof and raw_prof.exists() and cov is not None:
        c_tracker = cov.Coverage()
        c_tracker.merge_raw([raw_prof])

    summary = {
        "baseline": "ossfuzz",
        "build": build,
        "seconds": elapsed,
        "queries": total_execs,
        "exec_per_sec": qps or execs_per_sec,
        "libfuzzer_cov": cov_count,
        "libfuzzer_features": ft_count,
        "corpus_units": corp_count,
        "crashes": crashes,
        "returncode": proc.returncode,
    }
    print(f"\n[+] OSS-Fuzz Run Complete: {summary}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="OSS-Fuzz runner for DuckDB parse_fuzz_test")
    ap.add_argument("--minutes", type=float, default=0, help="Run budget in minutes")
    ap.add_argument("--seconds", type=float, default=60, help="Run budget in seconds")
    ap.add_argument("--runs", type=int, default=0, help="Max execution runs (0 for unlimited)")
    ap.add_argument("--build", choices=["asan", "cov"], default="asan", help="Target binary build")
    args = ap.parse_args()

    secs = args.minutes * 60 if args.minutes > 0 else args.seconds
    run_ossfuzz(seconds=secs, build=args.build, max_runs=args.runs)


if __name__ == "__main__":
    main()
