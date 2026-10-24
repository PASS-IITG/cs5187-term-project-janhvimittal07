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

_VOCAB: set[str] | None = None


def sql_vocab() -> set[str]:
    """SQL keywords + function names of the target build: everything else in a query is an identifier / alias."""
    global _VOCAB
    if _VOCAB is None:
        code = ("import duckdb;c=duckdb.connect();"
                "print(' '.join([r[0] for r in c.execute('select keyword_name from duckdb_keywords()').fetchall()] + "
                "[r[0] for r in c.execute('select distinct function_name from duckdb_functions()').fetchall()]))")
        out = subprocess.run([str(devcheck.DEV_PY), "-I", "-c", code], capture_output=True, text=True, timeout=120).stdout
        _VOCAB = {w.lower() for w in out.split()}
    return _VOCAB


def skeleton(sql: str) -> str:
    """Query shape with literals, identifiers and aliases removed (random alias names must not look like new templates)."""
    t = re.sub(r"'(?:[^']|'')*'", "S", sql.lower())
    t = re.sub(r"\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b", "N", t)
    vocab = sql_vocab()
    t = re.sub(r"\b[a-z_][a-z0-9_]*\b", lambda m: m.group(0) if m.group(0) in vocab or m.group(0) in ("s", "n") else "i", t)
    t = re.sub(r"\b(?:i\s*\.\s*)+i\b", "i", t)                 # a.b.c -> i
    t = re.sub(r"\bi(\s*,\s*i)+\b", "i", t)                      # column lists of different lengths -> i
    toks = re.findall(r"\w+|[^\w\s]", t)
    changed = True
    while changed:                                   # "+ N + N + N" -> "+ N"; IN-lists / ORDER BY lists of any length -> one
        changed = False
        for w in range(1, 9):
            i, out = 0, []
            while i < len(toks):
                if i + 2 * w <= len(toks) and toks[i:i + w] == toks[i + w:i + 2 * w]:
                    j = i + w
                    while j + w <= len(toks) and toks[j:j + w] == toks[i:i + w]:
                        j += w
                    out += toks[i:i + w]
                    i, changed = j, True
                else:
                    out.append(toks[i])
                    i += 1
            toks = out
    return " ".join(toks)


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

def plan_info(con, sql):
    """(operator-tree signature, expression-level signature) from EXPLAIN (FORMAT JSON); None if EXPLAIN fails.
    The tree is the shape of the physical plan; the expression-level text additionally contains each operator's details
    (conditions, projections, aggregates) with digits removed, so rewrites that do not change the operators still show."""
    try:
        j = json.loads(con.execute("EXPLAIN (FORMAT JSON) " + sql).fetchall()[0][1])
    except Exception:
        return None
    def tree(n):
        if isinstance(n, list): return "(" + ",".join(tree(x) for x in n) + ")"
        ch = n.get("children") or []
        return n.get("name", "?") + ("[" + ",".join(tree(c) for c in ch) + "]" if ch else "")
    def full(n):
        if isinstance(n, list): return "|".join(full(x) for x in n)
        ei = re.sub(r"\d+", "N", json.dumps(n.get("extra_info", {}), sort_keys=True))
        return n.get("name", "?") + ei + "[" + "|".join(full(c) for c in (n.get("children") or [])) + "]"
    return tree(j), full(j)

def strip_comments(sql):
    return re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S)

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
        # plan shape (always) and reach (only with a switch): does the plan change when the target switch is flipped?
        if st == "ok":
            a = plan_info(con, q["sql"])
            info["sig"] = hashlib.md5(a[0].encode()).hexdigest()[:10] if a else None
            if sw:
                b = plan_info(runs["switch_off"][0], q["sql"])
                info["reach"] = bool(a and b and a[1] != b[1])
                info["reach_tree"] = bool(a and b and a[0] != b[0])
        findings = []
        for name, (c, res) in runs.items():
            s2, v2 = res[i]
            if s2 in ("internal", "fatal"):
                findings.append({"kind": s2, "config": name, "detail": str(v2)[:200]})
        bare = strip_comments(q["sql"])          # words inside comments must not switch the comparison off; the "unordered" flag is ignored (rows are compared as sorted lists anyway)
        comparable = not NONDET.search(bare) and not (LIMITY.search(bare) and "order by" not in bare.lower())
        info["comparable"] = bool(comparable and st == "ok")
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


def quality_verdict(*, nq: int, nvalid: int, templates: int, shapes: int, cases: int, reach_rate: float | None,
                    comparable_rate: float, has_switch: bool, target=None) -> tuple[list[str], list[str]]:
    """Hard flags (any one = LOW DIVERSITY) and soft warnings. Shared by the runner and by `targeted_run.py --audit`,
    which recomputes the verdict from the stored counts instead of trusting the stored word."""
    flags, warns = [], []
    need_t = max(30, int(0.02 * nvalid))          # distinct SHAPES of valid queries (identifiers / literals removed, repeats collapsed)
    need_s = max(15, int(0.03 * cases))           # ~30 distinct physical-plan OPERATOR TREES per 1000 cases, switch or not
    if templates < need_t:
        flags.append(f"only {templates} distinct valid-query skeletons for {nvalid} valid queries (need >= {need_t})")
    if nvalid >= 100 and shapes < need_s:
        flags.append(f"only {shapes} distinct physical-plan operator trees for {cases} cases (need >= {need_s})")
    if has_switch and nvalid >= 100 and (reach_rate or 0.0) < 0.3:
        flags.append(f"reach rate {(reach_rate or 0.0):.0%} < 30%: the queries rarely exercise the target ({target})")
    if nvalid >= 100 and comparable_rate < 0.5:
        flags.append(f"only {comparable_rate:.0%} of valid queries take part in the result comparison across configurations")
    if nq and nvalid == nq:
        warns.append("100% of queries are valid: a real generator hits some binder errors on unusual shapes")
    return flags, warns


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

    nq = nvalid = nreach = nreach_tree = ncomparable = 0
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
            if info["status"] == "ok":
                templates.add(skeleton(sql))
            if info["status"] == "ok":
                nvalid += 1
                ncomparable += 1 if info.get("comparable") else 0
                if info["sig"]:
                    sigs[info["sig"]] += 1
                nreach_tree += 1 if info.get("reach_tree") else 0
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
    flags, warns = quality_verdict(nq=nq, nvalid=nvalid, templates=len(templates), shapes=shapes, cases=len(results),
                                   reach_rate=(nreach / nvalid if nvalid else 0.0) if sw else None,
                                   comparable_rate=(ncomparable / nvalid if nvalid else 0.0), has_switch=bool(sw), target=target)
    comparable_rate = ncomparable / nvalid if nvalid else 0.0
    if len(per_case) == 1 and nq >= 200:
        warns.append(f"every case has exactly {next(iter(per_case))} queries (fine only if the queries themselves vary structurally)")
    if sw and nreach and nreach_tree / nreach < 0.3:
        warns.append("most reaching queries change only expressions, not operators (fine for expression-level optimizers, check the examples)")
    quality = "LOW DIVERSITY - NOT A REAL TEST" if flags else "ok"
    metrics = {
        "quality": quality, "quality_flags": flags, "quality_warnings": warns, "distinct_templates": len(templates),
        "author_model": meta.get("author_model"), "generator_sha256": hashlib.sha256(src.encode()).hexdigest(),
        "target_key": json.dumps(target, sort_keys=True), "comparable_rate": round(comparable_rate, 3),
        "reach_tree_rate": round(nreach_tree / nvalid, 3) if (nvalid and sw) else None,
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
         f"**Quality: {m['quality']}**" + ("".join(f"\n* {f}" for f in m["quality_flags"]) if m["quality_flags"] else "")
         + ("".join(f"\n* (warning) {w}" for w in m.get("quality_warnings", []))),
         f"* distinct query templates: {m['distinct_templates']}; queries per case: {m['queries_per_case']}", ""
         f"* cases generated {m['n_cases']}, ran {m['cases_run']}, setup valid {m['setup_ok']}",
         f"* queries {m['queries']}, valid {m['valid_queries']} (**valid rate {m['valid_rate']:.1%}**)"]
    if m["has_switch"]:
        L.append(f"* **reach rate {m['reach_rate']:.1%}** of valid queries: the plan changes when the target switch ({m['target']}) is flipped; "
                 f"{m['distinct_plan_signatures']} distinct operator trees")
    else:
        L.append(f"* no target switch for this feature, so reach cannot be measured; {m['distinct_plan_signatures']} distinct operator trees")
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
