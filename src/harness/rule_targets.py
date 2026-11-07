"""Rule cards: turn the optimizer's C++ and its git history into targets for the generator-writing LLM.

    python harness/rule_targets.py build [--since 2026-03-01] [--top 40]   # -> $FUZZ_WORK/results/targeted/rule_cards/*.md + index.json
    python harness/rule_targets.py show <name>                              # print one card

This automates step 1 of the method that found upstream 28527/28531/28532/28534 by hand: pick code that changes a lot and
gets fixed a lot, read its preconditions and the places where it builds new constants, and form a hypothesis there.
Per target (each expression rule in src/optimizer/rule/ and each switchable optimizer in src/optimizer/):
  * switch            how to turn exactly this code off (expression rules: the whole expression_rewriter)
  * markers           functions the rule matches (used for rule-level reach), "cast" for cast matchers
  * churn             commits / fix commits since --since, with subjects
  * fix tests         test files that those fix commits added or changed: a fix usually covers the reported case only,
                      the neighbours (other types, other operators) are where the next bug is (28532 was such a neighbour)
  * preconditions     every early `return nullptr` / `return false` with the condition lines before it
  * constant sites    lines that build or convert values (TryEvaluateScalar, Value::TIMESTAMP, NumericCast, casts, ...)
Cards are capped in size; the LLM is told the file path and may read the full source itself.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402
import devcheck  # noqa: E402

WORK = runner.WORK
SRC = WORK / "duckdb"
OUT = WORK / "results" / "targeted" / "rule_cards"
FIXY = re.compile(r"\b(fix|fixes|fixed|bug|crash|wrong|incorrect|overflow|out of range|internal|preserve|do not|don't|keep|handle|reject)\b", re.I)
CONST_SITE = re.compile(r"TryEvaluateScalar|Value::(TIMESTAMP|DATE|BIGINT|HUGEINT|INTEGER|DECIMAL|Numeric)\b|NumericCast|UnsafeNumericCast|"
                        r"Cast::Operation|TryCast|DefaultTryCastAs|DefaultCastAs|AddDefaultCastToType|AddCastToType|GetValue<|"
                        r"IntegralValue::Get|TryAddOperator|TryMultiplyOperator|TrySubtractOperator|Interval::|Timestamp::|Date::|"
                        r"return trunc;|MinimumValue|MaximumValue|NumericLimits")
# optimizer switch names whose source file name differs
FILE_TO_SWITCH = {"topn_window_elimination": "top_n_window_elimination", "remove_unused_columns": "unused_columns",
                  "filter_pullup": "filter_pullup", "filter_pushdown": "filter_pushdown", "statistics_propagator": "statistics_propagation",
                  "common_subplan_optimizer": "common_subplan", "remote_pushdown_optimizer": "remote_pushdown",
                  "join_filter_pushdown_optimizer": "join_filter_pushdown", "grouping_sets_optimizer": "grouping_sets",
                  "aggregate_function_rewriter": "aggregate_function_rewriter", "compressed_materialization": "compressed_materialization",
                  "build_probe_side_optimizer": "build_side_probe_side", "limit_pushdown": "limit_pushdown", "late_materialization": "late_materialization",
                  "cte_inlining": "cte_inlining", "sampling_pushdown": "sampling_pushdown", "regex_range_filter": "regex_range",
                  "in_clause_rewriter": "in_clause", "unnest_rewriter": "unnest_rewriter", "deliminator": "deliminator",
                  "column_lifetime_analyzer": "column_lifetime", "common_aggregate_optimizer": "common_aggregate",
                  "cse_optimizer": "common_subexpressions", "empty_result_pullup": "empty_result_pullup", "join_elimination": "join_elimination",
                  "window_self_join": "window_self_join", "projection_pullup": "projection_pullup", "outer_join_simplification": "outer_join_simplification",
                  "window_rewriter": "window_rewriter", "partial_aggregate_pushdown": "partial_aggregate_pushdown", "type_pushdown": "type_pushdown",
                  "scalar_fn_pushdown": "scalar_fn_pushdown", "distinct_aggregate_rewrite": "distinct_aggregate_rewrite", "aggregate_reuse": "aggregate_reuse",
                  "projection_placement": "projection_placement", "redundant_order_keys": "redundant_order_keys", "reorder_filter": "reorder_filter",
                  "duplicate_groups": "duplicate_groups", "row_group_pruner": "row_group_pruner", "topn_optimizer": "top_n",
                  "cte_filter_pusher": "cte_filter_pusher", "materialized_cte": "materialized_cte", "partitioned_execution": "partitioned_execution"}
NOT_TESTABLE = {"type_pushdown", "remote_pushdown"}          # need file readers / remote sources, which the generator contract forbids


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(SRC), *args], capture_output=True, text=True, errors="replace", timeout=300).stdout


def switches() -> set[str]:
    code = "import duckdb;print(' '.join(r[0] for r in duckdb.connect().execute('select name from duckdb_optimizers()').fetchall()))"
    return set(subprocess.run([str(devcheck.DEV_PY), "-I", "-c", code], capture_output=True, text=True, timeout=120).stdout.split())


def markers(text: str) -> list[str]:
    out: list[str] = []
    # Identifier("f"), SpecificFunctionMatcher("f"), identifier_set_t {..} / identifier_set_t name = {..}, ManyFunctionMatcher({..});
    # operators (+, ~~, ||) print infix in plans and cannot be markers, so only word-like names are kept
    for m in re.finditer(r'(?:Identifier|SpecificFunctionMatcher>?)\(\s*"(\w+)"\s*\)|identifier_set_t\s*(?:\w+\s*=\s*)?\{([^}]*)\}', text):
        out += [m.group(1)] if m.group(1) else re.findall(r'"([A-Za-z_]\w*)"', m.group(2))
    if re.search(r"CastExpressionMatcher|BoundCastExpression::IsCast", text):
        out.append("cast")
    return [x for x in dict.fromkeys(out) if not x.startswith("__")][:12]


def preconditions(lines: list[str], limit: int = 40) -> list[str]:
    out = []
    for i, ln in enumerate(lines):
        if re.search(r"return (nullptr|false);", ln):
            ctx = [l.rstrip() for l in lines[max(0, i - 3):i + 1] if l.strip()]
            out.append(f"L{i + 1}: " + " | ".join(c.strip() for c in ctx)[:260])
    return out[:limit]


def constant_sites(lines: list[str], limit: int = 30) -> list[str]:
    return [f"L{i + 1}: {ln.strip()[:200]}" for i, ln in enumerate(lines) if CONST_SITE.search(ln)][:limit]


def fix_tests(fix_shas: list[str], limit: int = 8) -> list[str]:
    tests: list[str] = []
    for sha in fix_shas[:12]:
        for f in git("show", "--name-only", "--format=", sha).split():
            if f.startswith("test/") and f not in tests:
                tests.append(f)
    return tests[:limit]


def score(is_rule: bool, markers: list[str], commits: int, fixes: int, value_sites: int) -> int:
    """Rank targets like the ones that produced the 2026-11-07 bugs (upstream 28527/28531/28532/28534/28535/28536): expression
    rules that BUILD new values (bounds, folded constants, casts) and have recent fixes. Raw churn is capped so that busy
    operator-level optimizers (heavily exercised upstream, and whose differences are mostly the accepted error-hiding kind)
    do not dominate the ranking."""
    return 2 * min(fixes, 6) + min(commits, 15) + 3 * min(value_sites, 10) + (15 if is_rule else 0) + (5 if is_rule and markers else 0)


def card(path: Path, since: str, sw_names: set[str]) -> dict | None:
    rel = str(path.relative_to(SRC))
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    is_rule = "/optimizer/rule/" in rel
    stem = path.stem
    switch = "expression_rewriter" if is_rule else FILE_TO_SWITCH.get(stem, stem if stem in sw_names else "")
    if not switch or switch not in sw_names or switch in NOT_TESTABLE or (not is_rule and stem == "expression_rewriter"):
        return None
    log = [l.split(" ", 1) for l in git("log", "--format=%h %s", f"--since={since}", "--", rel).splitlines() if " " in l]
    fixes = [(h, s) for h, s in log if FIXY.search(s) and not s.lower().startswith(("merge", "format", "apply clang"))]
    target = {"optimizer": switch}
    mk = markers(text) if is_rule else []
    if is_rule and mk:
        target.update(rule=stem, marker=mk)
    c = {"name": stem, "file": rel, "kind": "expression rule" if is_rule else "optimizer", "switch": switch, "target": target,
         "markers": mk, "commits": len(log), "fix_commits": len(fixes), "score": score(is_rule, mk, len(log), len(fixes), len(constant_sites(lines))),
         "fix_subjects": [s[:140] for _, s in fixes[:10]], "fix_tests": fix_tests([h for h, _ in fixes]),
         "preconditions": preconditions(lines), "constant_sites": constant_sites(lines), "lines": len(lines)}
    if is_rule and not mk:
        c["note"] = ("the rule matches operators or arbitrary functions (no marker function): reach is only measured at the expression "
                     "level, which the whole rewriter changes too - weak evidence; if the rule matches a named function, add it as "
                     "target['rule'] + target['marker'] yourself")
    return c


def render(c: dict) -> str:
    L = [f"# Target: `{c['name']}` ({c['kind']}, {c['file']}, {c['lines']} lines)", "",
         f"* switch to turn it off: `SET disabled_optimizers='{c['switch']}'`" + (" (the whole expression rewriter: judge on RULE reach)" if c["kind"] == "expression rule" else ""),
         f"* generator target: `{json.dumps(c['target'])}`", f"* churn since the cut-off: {c['commits']} commits, {c['fix_commits']} look like fixes",
         f"* read the full source yourself: `$FUZZ_WORK/duckdb/{c['file']}`", ""]
    if c.get("note"):
        L += [f"**Note:** {c['note']}", ""]
    if c["fix_subjects"]:
        L += ["## Recent fixes (each fixed ONE reported case; look for the neighbours it did not cover)", ""] + [f"* {s}" for s in c["fix_subjects"]] + [""]
    if c["fix_tests"]:
        L += ["## Tests those fixes added or changed (see which types/operators/edges they cover, then test the others)", ""] + [f"* `{t}`" for t in c["fix_tests"]] + [""]
    L += ["## Preconditions: every early exit (the rewrite only happens when none of these fire)", "", "```"] + c["preconditions"] + ["```", ""]
    if c["constant_sites"]:
        L += ["## Where it builds or converts values (bounds, folded constants, casts): check each for the edges of its type", "", "```"] + c["constant_sites"] + ["```", ""]
    return "\n".join(L)[:9000]


def build(since: str, top: int) -> list[dict]:
    sw = switches()
    cards = []
    for p in sorted((SRC / "src" / "optimizer").glob("*.cpp")) + sorted((SRC / "src" / "optimizer" / "rule").glob("*.cpp")):
        c = card(p, since, sw)
        if c:
            cards.append(c)
    cards.sort(key=lambda c: -c["score"])
    cards = cards[:top]
    OUT.mkdir(parents=True, exist_ok=True)
    for c in cards:
        (OUT / f"{c['name']}.md").write_text(render(c))
    (OUT / "index.json").write_text(json.dumps([{k: c[k] for k in ("name", "file", "kind", "switch", "target", "score", "commits", "fix_commits")} for c in cards], indent=1))
    return cards


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["build", "show"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--since", default="2026-03-01")
    ap.add_argument("--top", type=int, default=40)
    a = ap.parse_args()
    if a.cmd == "build":
        cards = build(a.since, a.top)
        for c in cards:
            print(f"{c['score']:4} {c['kind']:15} {c['name']:38} switch={c['switch']:28} markers={','.join(c['markers'])[:50]}")
        print(f"{len(cards)} cards -> {OUT}")
    else:
        print((OUT / f"{a.name}.md").read_text())


if __name__ == "__main__":
    main()
