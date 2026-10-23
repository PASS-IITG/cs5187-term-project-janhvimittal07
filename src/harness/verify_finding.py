"""Mechanical verification of a candidate finding: replay a SQL script alone on every build / setting and give a verdict.

    python harness/verify_finding.py script.sql [--dev-site DIR] [--search]        # prints one JSON object

What it runs (each in a FRESH process, statements one by one, continuing after errors like the shell does):
  wheel           the released 2.0.0 python wheel (this interpreter), plain
  wheel_nocte     same with SET delim_join_as_cte=false      (the new dependent-join rewrite is the usual suspect)
  wheel_threads1  same with SET threads=1
  wheel_noopt     same with every optimizer disabled         (separates optimizer bugs from binder/planner/execution bugs)
  wheel_verify    same under PRAGMA enable_verification
  asan            build-asan/duckdb (assertions + ASan + UBSan, built from the same release)
  dev             a newer development wheel unpacked in --dev-site (e.g. pip install --pre --target DIR duckdb)
Verdict:
  reproduces      INTERNAL / FATAL error, crash or sanitizer report on the wheel and/or ASan build, again in a fresh process
  asan_only       only the assertions+sanitizer build shows it (the release wheel gives a correct answer): lower severity
  not_reproducible  nothing bad happens alone: it was an artifact of the batch (memory cap, thread race, shared state)
With --search the normalised error text is searched in duckdb/duckdb issues and PRs (rate-limited) and the closest hits
are returned: a finding is only "possibly new" if nothing relevant comes back; a human must still read the hits.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402

WORK = runner.WORK

CHILD = r'''
import json, sys
dev = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else ""
if dev: sys.path.insert(0, dev)
import duckdb
d = json.load(sys.stdin)
c = duckdb.connect(":memory:")
for s in d["pre"]:
    c.execute(s)
c.execute("SET max_execution_time=30000")
res = {"version": duckdb.__version__, "internal": [], "errors": 0, "executed": 0}
for st in d["stmts"]:
    try:
        c.execute(st); res["executed"] += 1
    except Exception as e:
        m = str(e); n = type(e).__name__
        if n in ("InternalException", "FatalException") or "INTERNAL Error" in m or "FATAL Error" in m:
            res["internal"].append(" ".join(m.split("\n\nStack Trace")[0].split())[:240])
        else:
            res["errors"] += 1
print(json.dumps(res))
'''


def split(sql: str) -> list[str]:
    return [s.strip() for s in runner.split_statements(sql) if s.strip()]


def run_wheel(stmts: list[str], pre: list[str], python: str, dev_site: str = "") -> dict:
    try:
        p = subprocess.run([python, "-c", CHILD, "x", dev_site], input=json.dumps({"stmts": stmts, "pre": pre}),
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    if p.returncode != 0 or not p.stdout.strip():
        return {"status": "crash", "rc": p.returncode, "stderr": p.stderr.strip()[-200:]}
    r = json.loads(p.stdout.strip().splitlines()[-1])
    r["status"] = "internal" if r["internal"] else "ok"
    return r


def normalise(msg: str) -> str:
    """Error text without numbers / column bindings so that equal bugs compare equal."""
    return re.sub(r"\s+", " ", re.sub(r"#\[[0-9.]+\]|\b\d+\b|0x[0-9a-f]+|\[\d+\.\d+\]", "N", msg)).strip()[:160]


def search_terms(message: str) -> list[str]:
    """Search phrases from a raw error message: no numbers / bindings (GitHub search needs every word to match)."""
    msg = re.sub(r"\s+", " ", message)
    terms: list[str] = []
    m_expr = re.search(r"line \d+: (.{4,80})$", msg)
    m_file = re.search(r"/(\w+\.(?:cpp|hpp|h))\"? on line", msg)
    if "Assertion" in msg and m_expr:
        expr = re.sub(r"[^\w ()<>=!.&|]+", " ", m_expr.group(1)).strip()
        if m_file:
            terms.append(f"{m_file.group(1)} {expr}"[:90])
        terms.append(expr[:70])
    base = msg.split("INTERNAL Error:")[-1].strip()
    base = re.split(r"#\[|\[\d|\"|\d", base)[0].strip(" .:'")
    base = re.sub(r"\b(for|at|in|on|of|to|with|from)$", "", base).strip()
    if len(base) > 12 and "Assertion" not in base:
        terms += [base, " ".join(base.split()[:5])]
    out: list[str] = []
    for t in terms:
        if t and t not in out and len(t) > 10:
            out.append(t)
    return out[:3]


def search_upstream(message: str, pause: float = 8.0) -> list[dict]:
    """Search duckdb/duckdb issues + PRs (open and closed) for the error text. Returns [{kind, number, state, title}]."""
    out: list[dict] = []
    for term in search_terms(message):
        for kind in ("issues", "prs"):
            p = subprocess.run(["gh", "search", kind, "--repo", "duckdb/duckdb", term, "--limit", "6", "--sort", "updated",
                                "--json", "number,title,state,updatedAt"], capture_output=True, text=True, timeout=90)
            if p.returncode == 0 and p.stdout.strip():
                for o in json.loads(p.stdout):
                    out.append({"kind": kind[:-1], "number": o["number"], "state": o["state"], "title": o["title"][:110],
                                "updated": o["updatedAt"][:10], "matched": term})
            time.sleep(pause)
    seen, uniq = set(), []
    for o in out:
        if (o["kind"], o["number"]) not in seen:
            seen.add((o["kind"], o["number"]))
            uniq.append(o)
    return uniq


GENERIC_FRAMES = re.compile(r"^(Vector|DataChunk|Exception|InternalException|Throw|Assert|Value|string|vector|unique_ptr|shared_ptr|"
                            r"ExecutorTask|TaskScheduler|Pipeline|PipelineExecutor|Executor|ClientContext|Connection|main|"
                            r"__\w+|std|operator|memcpy|memmove|\w*Sanitizer\w*|Allocator|Catalog)$")


def frame_terms(frames: list[str]) -> list[str]:
    """Class names from the top stack frames (e.g. WindowDenseRankExecutor::GetData -> 'WindowDenseRankExecutor')."""
    out: list[str] = []
    for fr in frames:
        name = re.sub(r"^(duckdb::)+", "", fr)
        cls = re.split(r"::|<|\(", name)[0]
        if len(cls) > 6 and not GENERIC_FRAMES.match(cls) and cls not in out:
            out.append(cls)
    return out[:2]


def upstream_check(message: str, frames: list[str], pause: float = 8.0) -> tuple[list[str], list[dict]]:
    """(terms searched, hits). If no usable term exists the caller must NOT call the finding new."""
    terms = search_terms(message) if re.search(r"INTERNAL|Assertion|FATAL", message or "") else []
    terms += [t for t in frame_terms(frames) if t not in terms]
    hits: list[dict] = []
    for term in terms[:4]:
        for kind in ("issues", "prs"):
            p = subprocess.run(["gh", "search", kind, "--repo", "duckdb/duckdb", term, "--limit", "6", "--sort", "updated",
                                "--json", "number,title,state,updatedAt"], capture_output=True, text=True, timeout=90)
            if p.returncode == 0 and p.stdout.strip():
                hits += [{"kind": kind[:-1], "number": o["number"], "state": o["state"], "title": o["title"][:110],
                          "updated": o["updatedAt"][:10], "matched": term} for o in json.loads(p.stdout)]
            time.sleep(pause)
    seen, uniq = set(), []
    for h in hits:
        if (h["kind"], h["number"]) not in seen:
            seen.add((h["kind"], h["number"]))
            uniq.append(h)
    return terms[:4], uniq


def verify(sql: str, dev_site: str = "", search: bool = False) -> dict:
    stmts = split(sql)
    py = sys.executable
    optimizers = subprocess.run([py, "-c", "import duckdb;print(','.join(r[0] for r in duckdb.connect().execute('select name from duckdb_optimizers()').fetchall()))"],
                                capture_output=True, text=True).stdout.strip()
    runs = {
        "wheel": run_wheel(stmts, [], py),
        "wheel_nocte": run_wheel(stmts, ["SET delim_join_as_cte=false"], py),
        "wheel_threads1": run_wheel(stmts, ["SET threads=1"], py),
        "wheel_noopt": run_wheel(stmts, [f"SET disabled_optimizers='{optimizers}'"], py),
        "wheel_verify": run_wheel(stmts, ["PRAGMA enable_verification"], py),
    }
    if dev_site:
        runs["dev"] = run_wheel(stmts, [], py, dev_site)
    asan = runner.run_sql(sql, build="asan", timeout=300) if runner.BUILDS["asan"].exists() else None
    if asan is not None:
        runs["asan"] = {"status": asan.outcome, "signature": asan.signature, "errors": asan.errors[:2],
                        "frames": runner._frames(asan.stderr + "\n" + asan.stdout, 6)}

    wheel_bad = any(runs[k]["status"] in ("internal", "crash") for k in ("wheel", "wheel_verify", "wheel_threads1", "wheel_nocte", "wheel_noopt"))
    asan_bad = asan is not None and asan.outcome in ("internal_error", "asan", "crash")
    if wheel_bad:
        verdict = "reproduces"
    elif asan_bad:
        verdict = "asan_only"
    else:
        verdict = "not_reproducible"

    msgs = [m for r in runs.values() for m in r.get("internal", [])] + (asan.errors[:1] if asan_bad and asan.errors else [])
    msg = next((m for m in msgs if m), "") or (f"{asan.outcome}: {asan.signature}" if asan_bad else "")
    out = {
        "verdict": verdict,
        "wheel_version": runs["wheel"].get("version"),
        "signature": normalise(msg),
        "example_message": msg[:240],
        "runs": {k: {kk: vv for kk, vv in v.items() if kk in ("status", "internal", "rc", "version")} for k, v in runs.items()},
        "depends_on": {  # settings that make it go away (useful for the report)
            "delim_join_as_cte": bool(runs["wheel"]["status"] == "internal" and runs["wheel_nocte"]["status"] == "ok"),
            "threads": bool(runs["wheel"]["status"] == "internal" and runs["wheel_threads1"]["status"] == "ok"),
            "optimizers": bool(runs["wheel"]["status"] == "internal" and runs["wheel_noopt"]["status"] == "ok"),
        },
        "frames": runs.get("asan", {}).get("frames", []),
        "newer_dev_wheel": runs.get("dev", {}).get("status"),
        # reproduces on 2.0.0 but not on the newer development snapshot: already fixed upstream, not a new finding
        "fixed_in_dev": bool(verdict != "not_reproducible" and runs.get("dev", {}).get("status") == "ok"),
    }
    if search:
        out["search_terms"], out["upstream_hits"] = upstream_check(msg, out["frames"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script")
    ap.add_argument("--dev-site", default="")
    ap.add_argument("--search", action="store_true")
    a = ap.parse_args()
    print(json.dumps(verify(Path(a.script).read_text(), a.dev_site, a.search), indent=1))


if __name__ == "__main__":
    main()
