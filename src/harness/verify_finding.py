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


KNOWN_ISSUES_PATH = HERE / "known_issues.json"


def _strip_sql(script: str) -> str:
    """Script without comments and string literals: shape words in a comment or a string must not count."""
    t = re.sub(r"/\*.*?\*/|--[^\n]*", " ", script, flags=re.S)
    t = re.sub(r"'(?:[^']|'')*'", "''", t)
    return re.sub(r"\s+", " ", t.lower())


def _entry_hits(k: dict, message: str, frames: list[str]) -> bool:
    return bool(k.get("message_regex") and re.search(k["message_regex"], message or "")) or \
           bool(k.get("frames_regex") and any(re.search(k["frames_regex"], f) for f in frames or []))


def match_registry(message: str, frames: list[str], script: str, depends_on: dict | None = None,
                   other_messages: list[str] | None = None) -> list[dict]:
    """Compare a finding with the registry of issues we already know (known_issues.json), BEFORE any GitHub search.

    "rediscovery": same error AND the entry's required script shape (comments/strings ignored) AND, if listed, the same
                   `depends_on` settings (it disappears when they are switched off): an independent rediscovery.
    "sibling":     everything else that matches the error text: possibly the same code failing in a NEW way, so it is
                   surfaced to a human. Entries WITHOUT a shape can only ever give "sibling" (an error message alone is not proof).
                   If the script also raised errors that match no known issue, it is downgraded to "sibling" too."""
    try:
        reg = json.loads(KNOWN_ISSUES_PATH.read_text())
    except (OSError, ValueError):
        return []
    low = _strip_sql(script)
    unmatched = [m for m in (other_messages or []) if m and not any(_entry_hits(k, m, []) for k in reg)]
    out = []
    for k in reg:
        if not _entry_hits(k, message, frames):
            continue
        shape = k.get("shape_all")
        shape_ok = bool(shape) and all(t in low for t in shape)
        dep_ok = True if depends_on is None else all(depends_on.get(d) for d in k.get("depends_on", []))
        rel = "rediscovery" if (shape_ok and dep_ok and not unmatched) else "sibling"
        out.append({"issue": k["issue"], "ours": bool(k.get("ours")), "status": k.get("status", ""), "title": k.get("title", ""),
                    "relation": rel, "unmatched_other_errors": len(unmatched)})
    return out


# ---------------------------------------------------------------------------- wrong results (config differences)
# A wrong result has no error text, so the error-based path above cannot verify, deduplicate or search it. This path replays
# the script's LAST statement under the default configuration and under the alternative one (the SET/PRAGMA the hit was
# found with), each twice in fresh processes, plus a third vote with every optimizer disabled.
RESULT_CHILD = r'''
import json, sys, math
dev = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else ""
if dev: sys.path.insert(0, dev)
import duckdb
d = json.load(sys.stdin)
c = duckdb.connect(":memory:")
for s in ["SET max_execution_time=30000", "SET memory_limit='1GB'", "SET threads=2"] + d["pre"]:
    c.execute(s)
def norm(v):
    if isinstance(v, float):
        return "nan" if math.isnan(v) else repr(round(v, 9))
    if isinstance(v, (list, tuple)): return [norm(x) for x in v]
    if isinstance(v, dict): return {k: norm(x) for k, x in v.items()}
    return repr(v)
out = {"version": duckdb.__version__}
for st in d["setup"]:
    try: c.execute(st)
    except Exception as e: out.setdefault("setup_errors", []).append(str(e)[:120])
try:
    out["rows"] = sorted(json.dumps(norm(r)) for r in c.execute(d["query"]).fetchall())
except Exception as e:
    out["error"] = type(e).__name__ + ": " + " ".join(str(e).split())[:200]
print(json.dumps(out))
'''
# only the switches gen_runner prepends to a hit script count as the alternative configuration; any other leading SET
# (e.g. SET TimeZone in a generator's setup) belongs to the setup and must run in BOTH configurations
CONFIG_STMT = re.compile(r"^\s*(SET\s+(disabled_optimizers|delim_join_as_cte|threads)\s*=|PRAGMA\s+enable_verification)", re.I)


def _run_result(setup: list[str], query: str, pre: list[str], python: str, dev_site: str = "") -> dict:
    try:
        p = subprocess.run([python, "-c", RESULT_CHILD, "x", dev_site], input=json.dumps({"setup": setup, "query": query, "pre": pre}),
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    if p.returncode != 0 or not p.stdout.strip():
        return {"error": f"crash rc={p.returncode}"}
    return json.loads(p.stdout.strip().splitlines()[-1])


def split_diff_script(sql: str) -> tuple[list[str], list[str], str]:
    """(alternative-config statements, setup statements, compared query) of a gen_runner config_diff script:
    leading SET/PRAGMA lines are the configuration the result differed in, the last statement is the compared query."""
    stmts = split(sql)
    alt: list[str] = []
    if stmts and CONFIG_STMT.match(stmts[0]):
        alt.append(stmts.pop(0))
    if not stmts:
        return alt, [], ""
    return alt, stmts[:-1], stmts[-1]


def verify_diff(sql: str, dev_site: str = "", alt: list[str] | None = None) -> dict:
    """Verdict for a wrong-result candidate.
    wrong_result      default and alternative config each give the same rows twice, and the two differ
    nondeterministic  a configuration disagrees with itself (unordered LIMIT, ties, races): not a finding as is
    not_reproducible  alone, both configurations agree
    `suspect` names the side that disagrees with the all-optimizers-off vote (the side that is probably wrong)."""
    alt_found, setup, query = split_diff_script(sql)
    alt = alt if alt is not None else alt_found
    py = sys.executable
    if not query or not alt:
        return {"verdict": "verify_error", "signature": "no query or no alternative configuration in the script", "fixed_in_dev": False}
    optimizers = subprocess.run([py, "-c", "import duckdb;print(','.join(r[0] for r in duckdb.connect().execute('select name from duckdb_optimizers()').fetchall()))"],
                                capture_output=True, text=True).stdout.strip()
    d1, d2 = _run_result(setup, query, [], py), _run_result(setup, query, [], py)
    a1, a2 = _run_result(setup, query, alt, py), _run_result(setup, query, alt, py)
    noopt = _run_result(setup, query, [f"SET disabled_optimizers='{optimizers}'"], py)
    key = lambda r: r.get("rows", r.get("error"))                          # noqa: E731
    out = {"kind": "wrong_result", "alt": alt, "query": query, "wheel_version": d1.get("version"),
           "rows_default": len(d1.get("rows", [])), "rows_alt": len(a1.get("rows", [])),
           "example_default": (d1.get("rows") or [d1.get("error")])[:3], "example_alt": (a1.get("rows") or [a1.get("error")])[:3]}
    if key(d1) != key(d2) or key(a1) != key(a2):
        out["verdict"] = "nondeterministic"
    elif "error" in d1 or "error" in a1:
        out["verdict"] = "error_diff" if key(d1) != key(a1) else "not_reproducible"
        # which side raised the error: the side the all-optimizers-off vote agrees with is probably right
        if out["verdict"] == "error_diff":
            out["suspect"] = ("default" if key(noopt) == key(a1) else "alternative" if key(noopt) == key(d1) else "unclear")
    elif key(d1) == key(a1):
        out["verdict"] = "not_reproducible"
    else:
        out["verdict"] = "wrong_result"
        out["suspect"] = ("default" if key(noopt) == key(a1) else "alternative" if key(noopt) == key(d1) else "unclear")
    out["fixed_in_dev"] = False
    if dev_site and out["verdict"] == "wrong_result":
        dd, da = _run_result(setup, query, [], py, dev_site), _run_result(setup, query, alt, py, dev_site)
        out["dev_version"] = dd.get("version")
        out["fixed_in_dev"] = key(dd) == key(da) and key(dd) == key(noopt)
    out["signature"] = f"{out['verdict']} [{' ; '.join(alt)}] default {out['rows_default']} rows vs {out['rows_alt']} rows"
    out["registry"] = match_registry_diff(sql, alt) if out["verdict"] in ("wrong_result", "error_diff") else []
    return out


def match_registry_diff(script: str, alt: list[str]) -> list[dict]:
    """Registry match for wrong results (entries with "kind": "wrong_result").
    rediscovery: every `script_regex_all` matches the script (comments removed, strings KEPT: a date_trunc part or a type
                 name lives in a literal) AND the alternative configuration matches `switch_regex` (the difference
                 disappears exactly when the switch that the known bug depends on is flipped)
    sibling:     the switch and the entry's `family_regex` (e.g. the function involved) match but the precise shape does not:
                 the same rule may be wrong in a new way, so a human must look"""
    try:
        reg = json.loads(KNOWN_ISSUES_PATH.read_text())
    except (OSError, ValueError):
        return []
    text = re.sub(r"\s+", " ", re.sub(r"/\*.*?\*/|--[^\n]*", " ", script, flags=re.S)).lower()
    sw = " ; ".join(alt).lower()
    out = []
    for k in reg:
        if k.get("kind") != "wrong_result" or not re.search(k.get("switch_regex", "$^"), sw):
            continue
        exact = bool(k.get("script_regex_all")) and all(re.search(r, text) for r in k["script_regex_all"])
        fam = bool(k.get("family_regex") and re.search(k["family_regex"], text))
        if exact or fam:
            out.append({"issue": k["issue"], "ours": bool(k.get("ours")), "status": k.get("status", ""), "title": k.get("title", ""),
                        "relation": "rediscovery" if exact else "sibling"})
    return out


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
    all_msgs = list(dict.fromkeys(m for r in runs.values() for m in r.get("internal", [])))
    out["internal_messages"] = [normalise(m) for m in all_msgs][:6]
    out["registry"] = match_registry(msg, out["frames"], sql, out["depends_on"], [m for m in all_msgs if m != msg])
    if search and not out["registry"]:
        out["search_terms"], out["upstream_hits"] = upstream_check(msg, out["frames"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script")
    ap.add_argument("--dev-site", default="")
    ap.add_argument("--search", action="store_true")
    ap.add_argument("--diff", action="store_true", help="wrong-result candidate (gen_runner config_diff script): compare default vs the leading SET/PRAGMA")
    a = ap.parse_args()
    if a.diff:
        print(json.dumps(verify_diff(Path(a.script).read_text(), a.dev_site), indent=1))
        return
    print(json.dumps(verify(Path(a.script).read_text(), a.dev_site, a.search), indent=1))


if __name__ == "__main__":
    main()
