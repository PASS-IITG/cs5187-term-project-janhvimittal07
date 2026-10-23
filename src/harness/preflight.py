"""Preflight: refuse to start a fuzzing run unless it is on the intended DuckDB release.

    python harness/preflight.py [--tag v2.0.0]

Checks, each PASS / FAIL / WARN:
  source   $FUZZ_WORK/duckdb is checked out at the tag (builds are made from it)
  wheel    the python `duckdb` in this interpreter is that release (same source id as the tag)
  dev      $FUZZ_WORK/.venv-dev (used by the generators' worker processes) is that release
  asan     build-asan/duckdb reports that source id and can run core functions (sum)
  driver   src/driver/driver-asan exists and runs core functions (a wrongly linked driver has none)
  newer    GitHub has no release newer than the tag (WARN only; needs `gh`)
  memory   enough free RAM for the ASan build / runs (WARN only)

A run on the wrong version proves nothing about the target release: our own first runs used a pre-release snapshot and a
v1.5.6 source tree before this check existed. Exit status is 1 if any check FAILs.
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
CODE = HERE.parent
WORK = Path(os.environ.get("FUZZ_WORK", CODE))

results: list[tuple[str, str, str]] = []


def rec(status: str, name: str, detail: str) -> None:
    results.append((status, name, detail))
    print(f"[{status:4}] {name:7} {detail}", flush=True)


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for v in ("AI_AGENT", "CLAUDECODE", "CLAUDE_CODE", "CURSOR_AGENT"):
        env.pop(v, None)                                   # the shell prints JSON errors in "agent mode"
    env.setdefault("ASAN_OPTIONS", "detect_leaks=0")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 60), env=env, **kw)


def wheel_info(py: str) -> tuple[str, str]:
    p = sh([py, "-c", "import duckdb;c=duckdb.connect();print(duckdb.__version__, c.execute('select source_id from pragma_version()').fetchone()[0])"])
    out = p.stdout.strip().split()
    return (out[0], out[1]) if len(out) == 2 else ("?", p.stderr.strip()[-80:])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default=(CODE / "DUCKDB_VERSION").read_text().strip() if (CODE / "DUCKDB_VERSION").exists() else "v2.0.0")
    a = ap.parse_args()
    want_ver = a.tag.lstrip("v")
    src = WORK / "duckdb"

    # source
    want_id = ""
    if (src / ".git").exists():
        desc = sh(["git", "-C", str(src), "describe", "--tags", "--exact-match"]).stdout.strip()
        head = sh(["git", "-C", str(src), "rev-parse", "--short=10", "HEAD"]).stdout.strip()
        tagc = sh(["git", "-C", str(src), "rev-parse", "--short=10", f"{a.tag}^{{commit}}"]).stdout.strip()
        want_id = tagc
        if desc == a.tag:
            rec("PASS", "source", f"{src} is at {a.tag} ({head})")
        else:
            rec("FAIL", "source", f"{src} is at {desc or head!r}, not {a.tag}: run `git -C {src} fetch --tags && git -C {src} checkout {a.tag}` and REBUILD")
    else:
        rec("FAIL", "source", f"{src} is not a git checkout")

    # wheels
    for name, py in (("wheel", sys.executable), ("dev", str(WORK / ".venv-dev" / "bin" / "python"))):
        if name == "dev" and not Path(py).exists():
            rec("FAIL", "dev", f"{py} missing: python3 -m venv {WORK}/.venv-dev && {WORK}/.venv-dev/bin/pip install duckdb=={want_ver}")
            continue
        ver, sid = wheel_info(py)
        ok = ver == want_ver and (not want_id or sid.startswith(want_id))
        rec("PASS" if ok else "FAIL", name, f"duckdb {ver} source_id {sid}" + ("" if ok else f"  (want {want_ver} {want_id}); pip install --force-reinstall duckdb=={want_ver}"))

    # sanitizer build
    asan = WORK / "build-asan" / "duckdb"
    if asan.exists():
        p = sh([str(asan), "-no-agent", ":memory:", "-init", "/dev/null", "-csv", "-c",
                "select version(), (select source_id from pragma_version()), sum(range) from range(10)"])
        row = p.stdout.strip().splitlines()[-1].split(",") if p.stdout.strip() else []
        if len(row) == 3 and row[1].strip('"').startswith(want_id or "") and row[2].strip('"') == "45":
            rec("PASS", "asan", f"{asan} is {row[0].strip(chr(34))} {row[1].strip(chr(34))}, core functions work")
        else:
            rec("FAIL", "asan", f"{asan} reports {p.stdout.strip()[-100:] or p.stderr.strip()[-100:]!r}; rebuild: FUZZ_WORK={WORK} JOBS=3 bash src/build.sh asan")
    else:
        rec("FAIL", "asan", f"{asan} missing: FUZZ_WORK={WORK} JOBS=3 bash src/build.sh asan   (about 1 hour, 7 GB RAM)")

    # batch driver
    drv = CODE / "driver" / "driver-asan"
    if drv.exists():
        q = '{"id":"c","sql":"select sum(range) from range(100)","timeout_ms":5000}\n'
        p = subprocess.run([str(drv), "--timeout=5", "--isolate"], input=q, capture_output=True, text=True, timeout=60,
                           env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
        o = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
        if o.get("outcome") == "ok":
            rec("PASS", "driver", "driver-asan runs core functions")
        else:
            rec("FAIL", "driver", f"driver-asan outcome {o.get('outcome')!r}: {str(o.get('stderr'))[:90]!r}; rebuild: make -C {CODE}/driver clean asan FUZZ_WORK={WORK}")
    else:
        rec("FAIL", "driver", f"{drv} missing: make -C {CODE}/driver asan FUZZ_WORK={WORK}")

    # newer release
    if sh(["which", "gh"]).returncode == 0:
        p = sh(["gh", "release", "list", "--repo", "duckdb/duckdb", "--limit", "5", "--exclude-pre-releases"])
        tags = re.findall(r"\b(v\d+\.\d+\.\d+)\b", p.stdout)
        newer = [t for t in tags if tuple(map(int, t[1:].split("."))) > tuple(map(int, a.tag[1:].split(".")))]
        rec("WARN" if newer else "PASS", "newer", f"newer release(s) exist: {newer}; update the wheel and tell the team" if newer else f"no release newer than {a.tag}")
    else:
        rec("WARN", "newer", "gh not installed: check https://github.com/duckdb/duckdb/releases yourself")

    # memory
    try:
        mem = {k: int(v.split()[0]) for k, v in (l.split(":") for l in open("/proc/meminfo"))}
        avail = mem["MemAvailable"] // 1024
        rec("PASS" if avail >= 4000 else "WARN", "memory", f"{avail} MB available" + ("" if avail >= 4000 else " (<4 GB: use JOBS=2 for builds, one ASan job at a time)"))
    except OSError:
        rec("WARN", "memory", "could not read /proc/meminfo (not Linux?)")

    bad = [r for r in results if r[0] == "FAIL"]
    print(f"\n{'PREFLIGHT FAILED' if bad else 'preflight ok'}: {len(bad)} failing, {sum(1 for r in results if r[0] == 'WARN')} warnings")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
