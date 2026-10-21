"""Check findings against the newest published DuckDB *development* build.

A bug in the pinned release is only interesting if it is still alive in current
development code.

CAVEAT (measured 2026-10-14, wheel 2.0.0.dev2610011535): PRAGMA enable_verification is
INERT on the dev wheel (verify_serializer / verify_external / verify_fetch_row are gone and
queries that must fail verification pass). Use it for crashes, internal errors and for
direct checks (e.g. json_serialize_sql -> json_deserialize_sql round trips), NOT as a
verification oracle. The dev wheel on PyPI is ~25 MB, so this is far cheaper than
building `main`. (It has no sanitizers; internal errors, verification mismatches
and crashes are still detected.)

    python harness/devcheck.py --install             # newest wheel -> $FUZZ_WORK/.venv-dev
    python harness/devcheck.py --version
    python harness/devcheck.py --run script.sql [--verify]
    python harness/devcheck.py --annotate results/hunt/unique.json

Library: available(), version(), run_script(sql, pre=(), timeout=60) -> dict,
         check(stmts, cfg_stmts) -> {available, version, outcome, alive}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402

WORK = runner.WORK
VENV = WORK / ".venv-dev"
DEV_PY = Path(os.environ.get("DUCKDB_DEV_PY", VENV / "bin" / "python"))
VERIFY_RX = re.compile(r"statement differs from original result|verification failed|"
                       r"Unoptimized Result differs|result differs", re.I)
CRASH_RC = {-11, -6, -4, -8, 134, 139, 132, 136}

_CHILD = r'''
import duckdb, json, sys
d = json.load(sys.stdin)
c = duckdb.connect(":memory:")
errs = []
for s in d["pre"] + d["stmts"]:
    try:
        c.execute(s)
        try:
            c.fetchall()
        except Exception:
            pass
    except Exception as e:
        errs.append([type(e).__name__, str(e)[:600]])
print(json.dumps({"version": duckdb.__version__, "errors": errs}))
'''


def available() -> bool:
    return DEV_PY.exists()


def _compatible(name: str) -> bool:
    """Linux x86_64 wheel usable by this interpreter: exact cpXY or a stable-ABI (abi3) wheel."""
    if not (name.endswith(".whl") and "manylinux" in name and "x86_64" in name):
        return False
    py = sys.version_info.minor
    if f"-cp3{py}-cp3{py}-" in name:
        return True
    m = re.search(r"-cp3(\d+)-abi3-", name)
    return bool(m and int(m.group(1)) <= py)


def install() -> str:
    """Download the newest published duckdb wheel (dev or stable) into a private venv."""
    meta = json.load(urllib.request.urlopen("https://pypi.org/pypi/duckdb/json", timeout=60))
    best = None
    for ver, files in meta["releases"].items():
        for f in files:
            n = f["filename"]
            if _compatible(n) and (best is None or f["upload_time"] > best[0]):
                best = (f["upload_time"], ver, f["url"], n)
    if best is None:
        raise SystemExit(f"no manylinux x86_64 wheel for python 3.{sys.version_info.minor}")
    _, ver, url, name = best
    cache = WORK / "wheels"
    cache.mkdir(exist_ok=True)
    whl = cache / name
    if not whl.exists():
        print(f"downloading {name} ...", flush=True)
        # curl first: Python's urllib hit SSL EOF errors against the PyPI CDN on some networks
        r = subprocess.run(["curl", "-fsSL", "--retry", "3", "--max-time", "600", "-o", str(whl), url])
        if r.returncode != 0 or not whl.exists() or whl.stat().st_size < 1_000_000:
            whl.unlink(missing_ok=True)
            urllib.request.urlretrieve(url, whl)
    if not DEV_PY.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    subprocess.run([str(VENV / "bin" / "pip"), "-q", "install", "--no-deps", "--force-reinstall", str(whl)],
                   check=True)
    return ver


def run_script(sql: str, pre: list[str] | tuple = (), timeout: float = 60.0) -> dict:
    from runner import split_statements
    stmts = split_statements(sql)
    try:
        p = subprocess.run([str(DEV_PY), "-c", _CHILD], input=json.dumps({"pre": list(pre), "stmts": stmts}),
                           capture_output=True, text=True, timeout=timeout, cwd=WORK / "duckdb")
    except subprocess.TimeoutExpired:
        return {"outcome": "timeout", "errors": [], "version": None}
    if p.returncode != 0:
        return {"outcome": "crash" if p.returncode in CRASH_RC or p.returncode < 0 else "error",
                "errors": [["process", f"rc={p.returncode} {p.stderr[-300:]}"]], "version": None}
    try:
        r = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:                              # noqa: BLE001
        return {"outcome": "error", "errors": [["parse", p.stdout[-200:]]], "version": None}
    outcome = "ok"
    for cls, msg in r["errors"]:
        if cls == "InternalException":
            outcome = "internal_error"
            break
        if VERIFY_RX.search(msg):
            outcome = "verify_diff"
            break
    r["outcome"] = outcome
    return r


def setting_works(mode: str = "VERIFY_VECTORS") -> tuple[bool, str]:
    """Is `SET GLOBAL debug_verification_mode` accepted, and does it read back? (v2.0 verification)"""
    code = ("import duckdb\nc=duckdb.connect()\n"
            f"c.execute(\"SET GLOBAL debug_verification_mode='{mode}'\")\n"
            "print(c.execute(\"SELECT current_setting('debug_verification_mode')\").fetchone()[0])")
    try:
        p = subprocess.run([str(DEV_PY), "-c", code], capture_output=True, text=True, timeout=60)
    except Exception as e:                         # noqa: BLE001
        return False, repr(e)
    out = (p.stdout or "").strip()
    return (p.returncode == 0 and out.upper() == mode), (out or p.stderr[-200:])


_RT_CHILD = r'''
import duckdb, json, re, sys
d = json.load(sys.stdin)
c = duckdb.connect(":memory:")
RO = re.compile(r"^\s*(select|with|from|values|table)\b", re.I)
SKIP = re.compile(d["skip"], re.I)
finds = []
for s in d["stmts"]:
    q = s.strip().rstrip(";")
    if not RO.match(q) or SKIP.search(q):
        try: c.execute(s); c.fetchall()
        except Exception: pass
        continue
    try:
        j = c.execute("SELECT json_serialize_sql(?)", [q]).fetchone()[0]
        printed = c.execute("SELECT json_deserialize_sql(?)", [j]).fetchone()[0]
    except Exception:
        printed = None
    try: a = c.execute(q).fetchall(); aerr = None
    except Exception as e: a = None; aerr = type(e).__name__
    if not printed or aerr:
        continue
    try: b = c.execute(printed).fetchall(); berr = None
    except Exception as e: b = None; berr = type(e).__name__ + ": " + str(e).splitlines()[0][:80]
    if berr:
        finds.append(["roundtrip_reparse_fail", q[:300], printed[:300], berr])
    elif sorted(map(repr, a)) != sorted(map(repr, b)):
        finds.append(["roundtrip_result_diff", q[:300], printed[:300], ""])
print(json.dumps({"finds": finds}))
'''


def run_roundtrip(sql: str, skip: str, timeout: float = 90.0) -> dict:
    """Print every read-only query back to SQL (json_serialize_sql -> json_deserialize_sql) and
    check the printed text still parses and returns the same rows. Replaces the old
    'parsed statement' verifier, which found the COLLATE-in-BETWEEN bug."""
    from runner import split_statements
    try:
        p = subprocess.run([str(DEV_PY), "-c", _RT_CHILD], input=json.dumps({"stmts": split_statements(sql), "skip": skip}),
                           capture_output=True, text=True, timeout=timeout, cwd=WORK / "duckdb")
    except subprocess.TimeoutExpired:
        return {"outcome": "timeout", "finds": []}
    if p.returncode != 0:
        return {"outcome": "crash" if p.returncode < 0 else "error", "finds": [], "tail": p.stderr[-300:]}
    try:
        r = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:                              # noqa: BLE001
        return {"outcome": "error", "finds": []}
    r["outcome"] = "roundtrip" if r["finds"] else "ok"
    return r


def version() -> str | None:
    if not available():
        return None
    return run_script("SELECT 1;").get("version")


def check(stmts: list[str], cfg_stmts: list[str]) -> dict:
    """Does a (minimised) finding still fail on the dev build?"""
    if not available():
        return {"available": False, "version": None, "outcome": None, "alive": None}
    pre = [s.rstrip().rstrip(";") for s in cfg_stmts]
    r = run_script("\n".join(stmts) + "\n", pre)
    return {"available": True, "version": r.get("version"), "outcome": r["outcome"],
            "alive": r["outcome"] in ("internal_error", "verify_diff", "crash"),
            "first_error": (r["errors"][0] if r.get("errors") else None)}


def annotate(path: Path) -> None:
    items = json.loads(path.read_text())
    print(f"dev build: {version()}")
    print(f"{'signature':32s} {'v1.5.6 outcome':15s} -> dev build")
    for it in items:
        d = check(it["minimal_sql"], it.get("config_stmts", []))
        it["dev_build"] = d
        status = "STILL FAILS" if d.get("alive") else ("fixed / not reproducible" if d.get("available") else "n/a")
        print(f"{it['sig']:32s} {it['outcome']:15s} -> {status} ({d.get('outcome')})")
    path.write_text(json.dumps(items, indent=1, default=str))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--run")
    ap.add_argument("--verify", action="store_true", help="prepend PRAGMA enable_verification")
    ap.add_argument("--annotate")
    a = ap.parse_args()
    if a.install:
        print("installed", install())
    if a.version:
        print(version())
    if a.run:
        r = run_script(Path(a.run).read_text(), ["PRAGMA enable_verification"] if a.verify else [])
        print(json.dumps(r, indent=1))
    if a.annotate:
        annotate(Path(a.annotate))


if __name__ == "__main__":
    main()
