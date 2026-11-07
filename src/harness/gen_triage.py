"""Automatic triage of GENERATOR hits (gen_runner hits.jsonl): the manual steps that turned the 2026-11-07 hits into upstream
issues. (harness/triage.py is the older triage of fuzz.py / hunt.py findings; this one understands generator hit records,
the known-issues registry, wrong-result verification and the main nightly CLI.)

    python harness/gen_triage.py HITS.jsonl [--out DIR] [--rule NAME] [--no-search]

Per unique hit, regardless of the generator's quality verdict:
  1. direction  error_diff where only the switch-OFF config errors = the optimizer hides an error: labelled low priority
  2. verify     INTERNAL/FATAL/crash -> verify_finding.verify (fresh process, ASan build, dev snapshot, registry);
                config_diff / error_diff -> verify_finding.verify_diff (two runs per config, all-optimizers-off vote, registry)
  3. minimize   statement-level then VALUES-row-level delta reduction, keeping the same verdict
  4. main       the minimized script on the newest main nightly CLI (harness/nightly.py): still failing on main?
  5. upstream   gh search by the normalised error text, or (no error text) by the rule name and the functions in the query
  -> class: candidate_new | sibling | rediscovery | fixed_in_dev | fixed_on_main | low_priority | artifact | nondeterministic
Writes DIR/<class>_<id>.md per candidate/sibling and appends DIR/triage.jsonl. Never files anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402
import verify_finding as vf  # noqa: E402

WORK = runner.WORK
DEV = WORK / "dev-snapshot"
ERRORY = ("internal", "fatal", "crash", "timeout")
FUNC = re.compile(r"\b([a-z_][a-z0-9_]{2,})\s*\(", re.I)
SQL_WORDS = {"select", "cast", "count", "sum", "from", "where", "values", "insert", "into", "create", "table", "exists", "coalesce", "not",
             "hash", "try_cast", "min", "max", "avg"}


# ---------------------------------------------------------------------------- predicates kept while minimizing
def _internal_sig(stmts: list[str]) -> str:
    r = vf.run_wheel(stmts, [], sys.executable)
    if r.get("status") == "crash":
        return "crash"
    return vf.normalise(r["internal"][0])[:90] if r.get("internal") else ""


def _diff_holds(stmts: list[str]) -> bool:
    alt, setup, q = vf.split_diff_script(";\n".join(stmts) + ";")
    if not alt or not q:
        return False
    a = vf._run_result(setup, q, [], sys.executable)
    b = vf._run_result(setup, q, alt, sys.executable)
    return a.get("rows", a.get("error")) != b.get("rows", b.get("error"))


def minimize(sql: str, kind: str, budget: int = 120) -> str:
    """Greedy delta reduction: drop whole statements, then rows of multi-row INSERT ... VALUES lists (at most `budget` probes)."""
    stmts = vf.split(sql)
    probes = [0]
    if kind in ERRORY:
        target = _internal_sig(stmts)
        if not target:
            return sql
        def holds(ss):
            probes[0] += 1
            return _internal_sig(ss) == target
    else:
        if not _diff_holds(stmts):
            return sql
        def holds(ss):
            probes[0] += 1
            return _diff_holds(ss)
    keep = [s for s in stmts if vf.CONFIG_STMT.match(s)]               # the alternative config stays first
    body = [s for s in stmts if not vf.CONFIG_STMT.match(s)]
    i = 0
    while i < len(body) - 1 and probes[0] < budget:                    # never drop the last statement (the failing query)
        trial = keep + body[:i] + body[i + 1:]
        if holds(trial):
            body = body[:i] + body[i + 1:]
        else:
            i += 1
    for idx in range(len(body) - 1):
        m = re.match(r"(?is)(insert\s+into\s+\S+(?:\s*\([^)]*\))?\s+values\s*)(.*)$", body[idx])
        if not m:
            continue
        rows = re.findall(r"\((?:[^()']|'(?:[^']|'')*'|\((?:[^()']|'(?:[^']|'')*'|\((?:[^()']|'(?:[^']|'')*')*\))*\))*\)", m.group(2))
        j = 0
        while j < len(rows) and len(rows) > 1 and probes[0] < budget:
            cand = rows[:j] + rows[j + 1:]
            stmt = m.group(1) + ", ".join(cand)
            if holds(keep + body[:idx] + [stmt] + body[idx + 1:]):
                rows, body[idx] = cand, stmt
            else:
                j += 1
    return ";\n".join(keep + body) + ";\n"


# ---------------------------------------------------------------------------- main nightly
def on_main(sql: str, kind: str, signature: str) -> dict:
    try:
        import nightly
        binp = nightly.latest_cli()
        ver = nightly.version(binp)
    except Exception as e:                                              # noqa: BLE001
        return {"status": "unavailable", "detail": str(e)[:120]}
    if kind in ERRORY:
        rc, out = nightly.run_sql(binp, sql)
        hit = "INTERNAL Error" in out or "FATAL" in out or rc < 0
        return {"status": "fails" if hit else "passes", "version": ver,
                "same_message": bool(signature) and signature[:40] in vf.normalise(out)}
    alt, setup, q = vf.split_diff_script(sql)
    base = (";\n".join(setup) + ";\n") if setup else ""
    wrapped = f"SELECT * FROM ({q.rstrip(';')}) ORDER BY ALL;"
    _, a = nightly.run_sql(binp, base + wrapped)
    _, b = nightly.run_sql(binp, ";\n".join(alt) + ";\n" + base + wrapped)
    return {"status": "fails" if a.strip() != b.strip() else "passes", "version": ver}


# ---------------------------------------------------------------------------- upstream search without (usable) error text
def diff_search(sql: str, rule: str | None, msg: str = "") -> tuple[list[str], list[dict]]:
    stmts = vf.split(sql)
    q = stmts[-1] if stmts else ""
    funcs = [f.lower() for f in FUNC.findall(q) if f.lower() not in SQL_WORDS]
    terms = []
    if msg and len(msg) > 15 and "Error" in msg:
        terms.append(re.split(r"\d|'|\"", vf.normalise(msg))[0].strip()[:70])
    if rule:
        terms.append(rule.replace("_", " "))
    if funcs:
        terms.append(" ".join(dict.fromkeys(funcs[:3])))
    hits: list[dict] = []
    for term in [t for t in terms if len(t) > 6][:3]:
        for kind in ("issues", "prs"):
            p = vf.subprocess.run(["gh", "search", kind, "--repo", "duckdb/duckdb", term, "--limit", "5", "--sort", "updated",
                                   "--json", "number,title,state,updatedAt"], capture_output=True, text=True, timeout=90)
            if p.returncode == 0 and p.stdout.strip():
                hits += [{"kind": kind[:-1], "number": o["number"], "state": o["state"], "title": o["title"][:110], "matched": term}
                         for o in json.loads(p.stdout)]
            time.sleep(6)
    seen, uniq = set(), []
    for h in hits:
        if (h["kind"], h["number"]) not in seen:
            seen.add((h["kind"], h["number"]))
            uniq.append(h)
    return terms, uniq


# ---------------------------------------------------------------------------- one hit
def triage_hit(h: dict, rule: str | None = None, search: bool = True) -> dict:
    kind, script = h["kind"], h.get("script") or ""
    out = {"id": h.get("id"), "gen": h.get("gen"), "kind": kind, "config": h.get("config"), "detail": str(h.get("detail", ""))[:200]}
    if not script:
        out.update(cls="artifact", why="no script (worker-level problem)")
        return out
    if kind == "error_diff" and str(h.get("detail", "")).startswith("default ok"):
        out.update(cls="low_priority", why="only the switch-off config errors: the optimizer hides an error (upstream accepts this)")
        return out
    if kind in ERRORY:
        v = vf.verify(script, str(DEV) if DEV.exists() else "")
        out.update(verdict=v["verdict"], signature=v.get("signature", ""), message=v.get("example_message", ""), frames=v.get("frames", []),
                   depends_on={k: x for k, x in v.get("depends_on", {}).items() if x}, fixed_in_dev=v.get("fixed_in_dev"),
                   registry=v.get("registry") or [])
        bad = v["verdict"] in ("reproduces", "asan_only")
    else:
        v = vf.verify_diff(script, str(DEV) if DEV.exists() else "")
        out.update(verdict=v["verdict"], signature=v.get("signature", ""), suspect=v.get("suspect"), fixed_in_dev=v.get("fixed_in_dev"),
                   registry=v.get("registry") or [], rows=(v.get("rows_default"), v.get("rows_alt")),
                   message=str(v.get("example_default") if v.get("suspect") == "default" else v.get("example_alt"))[:200])
        bad = v["verdict"] in ("wrong_result", "error_diff")
    if not bad:
        out["cls"] = "nondeterministic" if out["verdict"] == "nondeterministic" else "artifact"
        return out
    reg = out["registry"]
    if any(r["relation"] == "rediscovery" for r in reg):
        out["cls"] = "rediscovery"
        return out
    if out.get("fixed_in_dev"):
        out["cls"] = "fixed_in_dev"
        return out
    out["minimized"] = minimize(script, kind)
    out["main"] = on_main(out["minimized"], kind, out.get("signature", ""))
    if out["main"].get("status") == "passes":
        out["cls"] = "fixed_on_main"
        return out
    if search:
        terms, ups = (vf.upstream_check(out.get("message", ""), out.get("frames", [])) if kind in ERRORY else ([], []))
        if not terms:
            terms, ups = diff_search(out["minimized"], rule, out.get("message", ""))
        out["search_terms"], out["upstream"] = terms, ups
    out["cls"] = "sibling" if reg else "candidate_new"
    return out


def render(t: dict) -> str:
    L = [f"# {t['cls'].upper()}: {t['kind']} from `{t.get('gen')}` ({t.get('id')})", "",
         f"* verdict: **{t.get('verdict')}**" + (f" (probably wrong side: {t.get('suspect')})" if t.get("suspect") else ""),
         f"* signature: `{t.get('signature', '')}`", f"* message: {t.get('message', '')}",
         f"* depends on: {t.get('depends_on') or t.get('config')}", f"* main nightly: {t.get('main')}",
         f"* registry: {', '.join(str(r['issue']) + ' ' + r['relation'] for r in t.get('registry') or []) or 'no match'}",
         f"* upstream search ({t.get('search_terms')}):"] + [f"  * {u['kind']} #{u['number']} ({u['state']}): {u['title']}" for u in (t.get("upstream") or [])[:8]]
    L += ["", "## Minimized reproducer", "", "```sql", (t.get("minimized") or "").strip(), "```", "",
          "A human must still confirm which side is right, read the rule's code, re-run on the newest nightly, search upstream by hand "
          "and decide whether to file. Nothing has been filed."]
    return "\n".join(L)


ORDER = {"internal": 0, "fatal": 0, "crash": 0, "config_diff": 1, "self_check": 1, "error_diff": 2, "timeout": 3}


def triage_file(hits_path: Path, out_dir: Path, rule: str | None = None, search: bool = True, max_hits: int = 12) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    hits = [json.loads(l) for l in hits_path.read_text().splitlines() if l.strip()] if hits_path.exists() else []
    hits.sort(key=lambda h: (ORDER.get(h["kind"], 4), str(h.get("detail", "")).startswith("default ok")))
    res, seen = [], set()
    for h in hits[:max_hits]:
        t = triage_hit(h, rule, search)
        sig = f"{t['cls']}|{vf.normalise(str(t.get('signature', '')))[:60]}"
        if t["cls"] in ("candidate_new", "sibling") and sig in seen:
            t["cls"] = "duplicate_in_run"
        seen.add(sig)
        res.append(t)
        if t["cls"] in ("candidate_new", "sibling"):
            name = hashlib.md5((t.get("minimized") or str(t["id"])).encode()).hexdigest()[:10]
            (out_dir / f"{t['cls']}_{name}.md").write_text(render(t))
        with open(out_dir / "triage.jsonl", "a") as fh:
            fh.write(json.dumps(t) + "\n")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hits")
    ap.add_argument("--out", default="")
    ap.add_argument("--rule", default=None)
    ap.add_argument("--no-search", action="store_true")
    ap.add_argument("--max-hits", type=int, default=12)
    a = ap.parse_args()
    out = Path(a.out) if a.out else Path(a.hits).parent / "triage"
    for t in triage_file(Path(a.hits), out, a.rule, not a.no_search, a.max_hits):
        print(f"{t['cls']:16} {t['kind']:12} {str(t.get('verdict')):16} {str(t.get('signature', ''))[:70]}")


if __name__ == "__main__":
    main()
