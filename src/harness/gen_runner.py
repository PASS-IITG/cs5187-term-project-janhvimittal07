"""Run LLM-written *generator programs* against DuckDB 2.0.0 and measure, from the ENGINE, whether they reach their target.

An LLM call produces one small program that yields thousands of test cases (this is how the new-feature fuzzers that found
duckdb/duckdb#27759 were built, and what the six AIxCC teams that used LLMs for seeds converged on). The model is the
author of the generator; this runner is the oracle and the judge. Nothing the model *says* about its generator is trusted.

    python harness/gen_runner.py check  GEN.py                 # AST allowlist + 30-case dry run + validity report
    python harness/gen_runner.py run    GEN.py --n 2000 --seed 1   # full run -> results/targeted/gens/<name>/
    python harness/gen_runner.py feedback GEN.py                # prints the feedback file for the next LLM round
    python harness/gen_runner.py dump   GEN.py --n 300 --out DIR   # write cases as .sql scripts (for asan_replay.py --sql-dir DIR)

GENERATOR CONTRACT (a plain python file, pure and deterministic per seed):

    META = {"name": "outer_join_simplification",
            "target": {"optimizer": "outer_join_simplification"},   # or {"setting": "delim_join_as_cte"} or {}  (switch to flip)
            "description": "what new code this aims at and why"}
    def generate(rng):                 # rng = random.Random(seed); return one test case
        return {"setup": ["CREATE TABLE ...", "INSERT ..."],      # self-contained; no files / network / extensions
                "queries": ["SELECT ...", {"sql": "SELECT ...", "unordered": True}]}   # str or dict

ALLOWED: imports of random, itertools, string, math, textwrap, functools, collections only; no open/exec/eval/getattr/
dunder access/global state. Anything else is rejected before a single line runs.

JUDGE (all measured by the engine, per case, each config on a FRESH connection):
  configs    default | delim_join_as_cte=false | target optimizer disabled | threads=1 | PRAGMA enable_verification
  findings   INTERNAL/FATAL error or crash in any config; result differing between configs (as multisets); ok-vs-error
             between configs is only noted for review
  reached    EXPLAIN (operator sequence) changes when the target switch is flipped: the query really exercises the target
  feedback   valid-query rate, reach rate, distinct plan signatures, top error messages, example reaching / missing queries
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import devcheck  # noqa: E402
import runner  # noqa: E402

WORK = runner.WORK
OUT = WORK / "results" / "targeted" / "gens"

ALLOWED_IMPORTS = {"random", "itertools", "string", "math", "textwrap", "functools", "collections"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "globals", "locals", "vars", "getattr", "setattr", "delattr",
                "breakpoint", "exit", "quit", "help", "memoryview", "type", "super", "classmethod", "staticmethod", "property"}
SAFE_BUILTINS = ["abs", "all", "any", "bool", "chr", "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset", "int",
                 "isinstance", "len", "list", "map", "max", "min", "ord", "pow", "range", "repr", "reversed", "round", "set",
                 "slice", "sorted", "str", "sum", "tuple", "zip", "ValueError", "KeyError", "IndexError", "Exception", "True",
                 "False", "None", "hash", "iter", "next", "bytes", "ascii", "bin", "hex", "oct", "callable"]


# ---------------------------------------------------------------------------- static check
def static_check(src: str) -> list[str]:
    """Return a list of violations (empty = acceptable)."""
    problems: list[str] = []
    if len(src) > 40_000:
        problems.append("file larger than 40 KB")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    for node in tree.body:                                   # no side effects at import time
        if not isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.FunctionDef, ast.Expr)):
            problems.append(f"line {node.lineno}: top level may only contain imports, assignments, functions (got {type(node).__name__})")
        if isinstance(node, ast.Expr) and not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            problems.append(f"line {node.lineno}: top-level expression statements are not allowed (docstrings only)")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    problems.append(f"line {node.lineno}: import {a.name} not allowed")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                problems.append(f"line {node.lineno}: from {node.module} import ... not allowed")
        elif isinstance(node, ast.Name) and (node.id in BANNED_NAMES or node.id.startswith("__")):
            problems.append(f"line {node.lineno}: name {node.id} not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append(f"line {node.lineno}: dunder attribute {node.attr} not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom, ast.ClassDef)):
            problems.append(f"line {node.lineno}: {type(node).__name__} not allowed")
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if "generate" not in names:
        problems.append("missing def generate(rng)")
    if not any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "META" for t in n.targets) for n in tree.body):
        problems.append("missing META = {...}")
    return problems


# ---------------------------------------------------------------------------- generation (sandboxed child)
GEN_CHILD = r'''
import json, random, resource, signal, sys
resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
req = json.load(sys.stdin)
import builtins
safe = {n: getattr(builtins, n) for n in req["builtins"] if hasattr(builtins, n)}
real_import = builtins.__import__
def guarded(name, *a, **k):
    if name.split(".")[0] not in req["imports"]:
        raise ImportError(name)
    return real_import(name, *a, **k)
safe["__import__"] = guarded
env = {"__builtins__": safe, "__name__": "gen"}
exec(compile(req["src"], "gen.py", "exec"), env)
out = []
def alarm(*_): raise TimeoutError("generate() took > 5 s")
signal.signal(signal.SIGALRM, alarm)
for seed in req["seeds"]:
    try:
        signal.alarm(5)
        case = env["generate"](random.Random(seed))
        signal.alarm(0)
        setup = [str(s) for s in case.get("setup", [])][:80]
        qs = []
        for q in case.get("queries", [])[:40]:
            qs.append(q if isinstance(q, dict) else {"sql": str(q)})
        out.append({"seed": seed, "setup": setup, "queries": [{"sql": str(q["sql"])[:6000], "unordered": bool(q.get("unordered", False))} for q in qs]})
    except Exception as e:
        out.append({"seed": seed, "error": type(e).__name__ + ": " + str(e)[:120]})
print(json.dumps({"meta": env.get("META"), "cases": out}))
'''


def generate_cases(src: str, seeds: list[int]) -> dict:
    p = subprocess.run([sys.executable, "-I", "-c", GEN_CHILD], input=json.dumps({"src": src, "seeds": seeds, "builtins": SAFE_BUILTINS,
                                                                               "imports": sorted(ALLOWED_IMPORTS)}),
                       capture_output=True, text=True, timeout=900, cwd=str(WORK / "work-scratch") if (WORK / "work-scratch").exists() else None)
    if p.returncode != 0 or not p.stdout.strip():
        return {"meta": None, "cases": [], "fatal": (p.stderr.strip().splitlines() or ["generator crashed"])[-1][:200]}
    return json.loads(p.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------- execution (engine judge)
EXEC_CHILD = r'''
import json, re, sys, hashlib
import duckdb
d = json.load(sys.stdin)
NONDET = re.compile(r"\b(random|uuid|gen_random_uuid|now|current_(timestamp|time|date)|today|setseed|txid_current|sample|tablesample|"
                    r"version|getenv|sleep|any_value|arbitrary|first|last|histogram|mode|list|array_agg|string_agg|group_concat|"
                    r"arg_min|arg_max|min_by|max_by|bitstring_agg|approx_\w+|reservoir_quantile)\b|\busing\s+sample\b", re.I)
LIMITY = re.compile(r"\b(limit|offset|top)\b", re.I)

def norm(v):
    if isinstance(v, float):
        if v != v: return "NaN"
        return float(f"{v:.7g}")
    if isinstance(v, (list, tuple)): return tuple(norm(x) for x in v)
    if isinstance(v, dict): return tuple(sorted((k, norm(x)) for k, x in v.items()))
    return v

def classify(e):
    m = str(e); n = type(e).__name__
    if n == "FatalException" or "FATAL Error" in m: return "fatal"
    if n == "InternalException" or "INTERNAL Error" in m: return "internal"
    return "err"

def plan_sig(con, sql):
    try:
        text = "\n".join(str(r[1]) for r in con.execute("EXPLAIN " + sql).fetchall())
    except Exception:
        return None
    return " ".join(re.findall(r"\b[A-Z][A-Z_]{3,}\b", text))

def run_config(case, pre):
    con = duckdb.connect(":memory:")
    for s in ["SET max_execution_time=20000", "SET memory_limit='1GB'", "SET threads=2", "SET enable_external_access=false"] + pre:
        con.execute(s)
    ok = True
    for s in case["setup"]:
        try: con.execute(s)
        except Exception as e:
            return None
    res = []
    for q in case["queries"]:
        try:
            rows = sorted((repr(norm(r)) for r in con.execute(q["sql"]).fetchall()))
            res.append(("ok", rows))
        except Exception as e:
            k = classify(e)
            res.append((k, type(e).__name__ + ": " + " ".join(str(e).split("\n\nStack Trace")[0].split())[:200]))
    return con, res

for case in d["cases"]:
    print(json.dumps({"at": case["seed"]}), flush=True)
    cfgs = {"default": []}
    cfgs["nocte"] = ["SET delim_join_as_cte=false"]
    cfgs["threads1"] = ["SET threads=1"]
    cfgs["verify"] = ["PRAGMA enable_verification"]
    sw = d["switch"]
    if sw: cfgs["switch_off"] = [sw]
    runs = {}
    for name, pre in cfgs.items():
        r = run_config(case, pre)
        if r is None:
            runs = None; break
        runs[name] = r
    if runs is None:
        print(json.dumps({"case": case["seed"], "valid_setup": False}), flush=True); continue
    base = runs["default"][1]
    con = runs["default"][0]
    out = {"case": case["seed"], "valid_setup": True, "queries": []}
    for i, q in enumerate(case["queries"]):
        st, val = base[i]
        info = {"i": i, "status": st, "msg": val if st != "ok" else "", "reach": None, "sig": None}
        # reach: does EXPLAIN change when the target switch is flipped?
        if sw and st == "ok":
            a = plan_sig(con, q["sql"])
            c2 = runs["switch_off"][0]
            b = plan_sig(c2, q["sql"])
            info["sig"] = hashlib.md5((a or "").encode()).hexdigest()[:10]
            info["reach"] = bool(a and b and a != b)
        findings = []
        for name, (c, res) in runs.items():
            s2, v2 = res[i]
            if s2 in ("internal", "fatal"):
                findings.append({"kind": s2, "config": name, "detail": str(v2)[:200]})
        comparable = not q.get("unordered") and not NONDET.search(q["sql"]) and not (LIMITY.search(q["sql"]) and "order by" not in q["sql"].lower())
        if comparable and st == "ok":
            for name, (c, res) in runs.items():
                if name in ("default", "verify"): continue
                s2, v2 = res[i]
                if s2 == "ok" and v2 != val:
                    findings.append({"kind": "config_diff", "config": name, "detail": "default %d rows %s | %s %d rows %s" % (len(val), val[:2], name, len(v2), v2[:2])})
                elif s2 != "ok" and s2 not in ("internal", "fatal"):
                    findings.append({"kind": "error_diff", "config": name, "detail": "default ok | %s %s" % (name, str(v2)[:100])})
        info["findings"] = findings
        out["queries"].append(info)
    print(json.dumps(out), flush=True)
print(json.dumps({"done": True}), flush=True)
'''


def switch_statement(target: dict) -> str:
    if target.get("optimizer"):
        return f"SET disabled_optimizers='{target['optimizer']}'"
    if target.get("setting") == "delim_join_as_cte":
        return "SET delim_join_as_cte=false"
    if target.get("setting"):
        return f"SET {target['setting']}={target.get('off_value', 'false')}"
    return ""


def execute(cases: list[dict], switch: str, timeout: float = 900.0) -> tuple[list[dict], list[dict]]:
    """Run cases in worker processes (crash-isolated). Returns (per-case results, worker-level problems)."""
    results: list[dict] = []
    problems: list[dict] = []
    todo = [c for c in cases if "error" not in c]
    while todo:
        try:
            p = subprocess.run([str(devcheck.DEV_PY), "-I", "-c", EXEC_CHILD], input=json.dumps({"cases": todo, "switch": switch}),
                               capture_output=True, text=True, timeout=timeout)
            out, rc, hung = p.stdout, p.returncode, False
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            rc, hung = -999, True
        last, done = None, False
        for ln in out.splitlines():
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if "at" in o:
                last = o["at"]
            elif "done" in o:
                done = True
            elif "case" in o:
                results.append(o)
        if done:
            break
        problems.append({"seed": last, "kind": "timeout" if hung else "crash", "detail": f"worker rc={rc}"})
        if last is None:
            break
        todo = [c for c in todo if c["seed"] > last]
    return results, problems


# ---------------------------------------------------------------------------- orchestration
def run_generator(path: Path, n: int, seed: int, out_dir: Path | None = None, quiet: bool = False) -> dict:
    src = path.read_text()
    bad = static_check(src)
    if bad:
        return {"ok": False, "rejected": bad}
    gen = generate_cases(src, list(range(seed * 100_000, seed * 100_000 + n)))
    if gen.get("fatal"):
        return {"ok": False, "rejected": [gen["fatal"]]}
    meta = gen.get("meta") or {}
    name = re.sub(r"[^\w-]", "_", str(meta.get("name") or path.stem))
    target = meta.get("target") or {}
    sw = switch_statement(target)
    cases = gen["cases"]
    gen_errors = Counter(c["error"] for c in cases if "error" in c)
    ok_cases = [c for c in cases if "error" not in c and c["queries"]]
    results, problems = execute(ok_cases, sw)
    by_seed = {c["seed"]: c for c in ok_cases}
    out_dir = out_dir or OUT / name
    out_dir.mkdir(parents=True, exist_ok=True)

    nq = nvalid = nreach = ncomparable = 0
    templates: set[str] = set()
    per_case: Counter = Counter()
    sigs: Counter = Counter()
    errs: Counter = Counter()
    hits: list[dict] = []
    examples = {"reach": [], "miss": []}
    for r in results:
        if not r.get("valid_setup"):
            errs["(setup failed)"] += 1
            continue
        case = by_seed[r["case"]]
        per_case[len(case["queries"])] += 1
        for info in r["queries"]:
            nq += 1
            sql = case["queries"][info["i"]]["sql"]
            templates.add(re.sub(r"\s+", " ", re.sub(r"\b\d+(\.\d+)?\b", "N", re.sub(r"'[^']*'", "S", sql.lower()))))
            if info["status"] == "ok":
                nvalid += 1
                if info["sig"]:
                    sigs[info["sig"]] += 1
                if info["reach"]:
                    nreach += 1
                    if len(examples["reach"]) < 2:
                        examples["reach"].append(sql[:300])
                elif sw and len(examples["miss"]) < 2:
                    examples["miss"].append(sql[:300])
            elif info["status"] == "err":
                errs[re.sub(r"\d+", "N", str(info["msg"]))[:110]] += 1
            for f in info["findings"]:
                script = ";\n".join(case["setup"]) + ";\n" + sql.rstrip(";") + ";\n"
                if f["config"] == "verify":
                    script = "PRAGMA enable_verification;\n" + script
                elif f["config"] == "nocte":
                    script = "SET delim_join_as_cte=false;\n" + script
                elif f["config"] == "threads1":
                    script = "SET threads=1;\n" + script
                elif f["config"] == "switch_off" and sw:
                    script = sw + ";\n" + script
                hits.append({"kind": f["kind"], "id": f"{name}:{r['case']}:{info['i']}", "config": f["config"], "detail": f["detail"],
                             "script": script, "gen": name})
    for pr in problems:
        hits.append({"kind": pr["kind"], "id": f"{name}:{pr['seed']}", "config": "worker", "detail": pr["detail"], "script": "", "gen": name})
    uniq = {}
    for h in hits:
        uniq.setdefault((h["kind"], h["config"], re.sub(r"\d+", "N", h["detail"])[:80]), h)
    (out_dir / "hits.jsonl").write_text("".join(json.dumps(h) + "\n" for h in uniq.values()))
    shapes = len(sigs)
    flags = []
    need_t = max(30, int(0.05 * nq))
    need_s = max(15, int(0.02 * nvalid))
    if len(templates) < need_t:
        flags.append(f"only {len(templates)} distinct query templates (literals stripped) for {nq} queries (need >= {need_t})")
    if sw and nvalid >= 100 and shapes < need_s:
        flags.append(f"only {shapes} distinct plan shapes for {nvalid} valid queries (need >= {need_s})")
    if len(per_case) == 1 and nq >= 200:
        flags.append(f"every case has exactly {next(iter(per_case))} queries (no structural randomness)")
    if nq and nvalid == nq and len(templates) < max(60, int(0.1 * nq)):
        flags.append("100% valid with few templates: queries are trivially valid")
    quality = "LOW DIVERSITY - NOT A REAL TEST" if flags else "ok"
    metrics = {
        "quality": quality, "quality_flags": flags, "distinct_templates": len(templates),
        "queries_per_case": dict(sorted(per_case.items())),
        "generator": str(path), "name": name, "target": target, "n_cases": n, "generator_errors": dict(gen_errors),
        "cases_run": len(results), "setup_ok": sum(1 for r in results if r.get("valid_setup")),
        "queries": nq, "valid_queries": nvalid, "valid_rate": round(nvalid / nq, 3) if nq else 0,
        "reach_queries": nreach, "reach_rate": round(nreach / nvalid, 3) if (nvalid and sw) else None,
        "distinct_plan_signatures": len(sigs), "hits_total": len(hits), "hits_unique": len(uniq),
        "hit_kinds": dict(Counter(h["kind"] for h in uniq.values())), "worker_problems": problems[:3],
        "top_errors": errs.most_common(6), "examples": examples, "has_switch": bool(sw), "seed": seed,
        "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "devcheck": devcheck.version(),
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1))
    (out_dir / "feedback.md").write_text(feedback_text(metrics))
    metrics["ok"] = True
    metrics["out_dir"] = str(out_dir)
    return metrics


def feedback_text(m: dict) -> str:
    """What the next LLM round gets: measured by the engine, never by the model."""
    L = [f"# Feedback for generator `{m['name']}` (engine-measured, seed {m['seed']}, DuckDB {m['devcheck']})", "",
         f"**Quality: {m['quality']}**" + ("".join(f"\n* {f}" for f in m["quality_flags"]) if m["quality_flags"] else ""),
         f"* distinct query templates: {m['distinct_templates']}; queries per case: {m['queries_per_case']}", ""
         f"* cases generated {m['n_cases']}, ran {m['cases_run']}, setup valid {m['setup_ok']}",
         f"* queries {m['queries']}, valid {m['valid_queries']} (**valid rate {m['valid_rate']:.1%}**)"]
    if m["has_switch"]:
        L.append(f"* **reach rate {m['reach_rate']:.1%}** of valid queries: the plan changes when the target switch ({m['target']}) is flipped; "
                 f"{m['distinct_plan_signatures']} distinct plan shapes")
    else:
        L.append(f"* no target switch for this feature, so reach cannot be measured; {m['distinct_plan_signatures']} distinct plan shapes")
    L.append(f"* findings: {m['hits_unique']} unique {m['hit_kinds'] or ''}")
    if m["generator_errors"]:
        L += ["", "## generate() raised errors (fix these)", *[f"* {e} x{n}" for e, n in m["generator_errors"].items()]]
    if m["top_errors"]:
        L += ["", "## Most common SQL errors (make the generator avoid or deliberately keep them)", *[f"* x{n}: {e}" for e, n in m["top_errors"]]]
    if m["examples"]["reach"]:
        L += ["", "## Queries that REACHED the target (keep producing shapes like these)", *[f"```sql\n{q}\n```" for q in m["examples"]["reach"]]]
    if m["examples"]["miss"]:
        L += ["", "## Valid queries that did NOT reach the target (change what makes the target code fire)", *[f"```sql\n{q}\n```" for q in m["examples"]["miss"]]]
    L += ["", "## Next round", "Raise reach rate and plan-shape variety; add NULLs / empty tables / duplicates / nesting; keep valid rate high. "
          "Do not game these numbers: a generator whose queries are trivially valid but never reach the target is useless."]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["check", "run", "feedback", "dump"])
    ap.add_argument("gen")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    path = Path(a.gen)
    if a.cmd == "feedback":
        name = json.loads(path.read_text() and "{}") if False else None
        d = OUT / re.sub(r"[^\w-]", "_", path.stem)
        print((d / "feedback.md").read_text() if (d / "feedback.md").exists() else "no feedback yet: run the generator first")
        return
    if a.cmd == "dump":
        src = path.read_text()
        bad = static_check(src)
        if bad:
            print("REJECTED:"); [print("  -", r) for r in bad]
            sys.exit(2)
        gen = generate_cases(src, list(range(a.seed * 100_000, a.seed * 100_000 + a.n)))
        name = re.sub(r"[^\w-]", "_", str((gen.get("meta") or {}).get("name") or path.stem))
        d = Path(a.out) if a.out else WORK / "corpus" / "llm" / f"gen-{name}"
        d.mkdir(parents=True, exist_ok=True)
        k = 0
        for c in gen["cases"]:
            if "error" in c or not c["queries"]:
                continue
            body = ";\n".join(c["setup"]) + ";\n" + ";\n".join(q["sql"].rstrip(";") for q in c["queries"]) + ";\n"
            (d / f"{name}__{c['seed']}.sql").write_text(body)
            k += 1
        print(f"wrote {k} scripts to {d}")
        return
    n = 30 if a.cmd == "check" else a.n
    m = run_generator(path, n, a.seed, Path(a.out) if a.out else (OUT / "_check" if a.cmd == "check" else None))
    if not m.get("ok"):
        print("REJECTED:"); [print("  -", r) for r in m["rejected"]]
        sys.exit(2)
    print((Path(m["out_dir"]) / "feedback.md").read_text())
    print(f"hits: {m['hits_unique']} unique -> {m['out_dir']}/hits.jsonl")


if __name__ == "__main__":
    main()
