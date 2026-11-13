"""Collect every number used in the paper into paper/data/ (run from the repo root).

    FUZZ_WORK=$HOME/cs5187 python paper/scripts/collect_data.py

Sources: the hunt ledgers and run logs under $FUZZ_WORK/results/hunt/, the control metrics under
$FUZZ_WORK/results/targeted/controls/, the Round 2 summary in src/results/targeted/, the SQLsmith campaign report,
and the live state of our upstream issues and their fix pull requests (GitHub CLI, read-only).
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("FUZZ_WORK", Path.home() / "cs5187"))
OUT = REPO / "paper" / "data"

# issue -> (method, kind, short title); method: B feature fuzzers, C LLM generators, D-i interactive rule-targeted
# session, D-a automated hunt loop. Source of the method: docs/upstream-drafts/*.md and the hunt RUN_LOGs.
ISSUES = {
    27759: ("B", "internal", "FOR EACH ROW trigger into a table with a BEFORE INSERT trigger"),
    27850: ("C", "internal", "identical EXISTS blocks under UNION ALL (delim_join_as_cte=false)"),
    27904: ("C", "internal", "CROSS JOIN LATERAL with a nested LATERAL count(*)"),
    28052: ("C", "wrong", "negated DOUBLE compared with a NaN constant"),
    28527: ("D-i", "wrong", "date_trunc with a sub-day part on a DATE column"),
    28531: ("D-i", "error", "CAST(timestamp AS DATE) = date at the TIMESTAMP range limits"),
    28532: ("D-i", "internal", "TIMESTAMP_MS to TIMESTAMP_S cast at range end invalidates the database"),
    28534: ("D-i", "error", "date_trunc comparison near the end of the TIMESTAMP range"),
    28535: ("D-i", "internal", "UBIGINT constant above BIGINT max in x + c1 = c2"),
    28536: ("D-a", "wrong", "regex empty group (?:) at the pattern edge treated as an anchor"),
    28541: ("D-a", "wrong", "UHUGEINT constant above HUGEINT max wraps in x + c1 = c2"),
    28542: ("D-a", "wrong", "TRY_CAST(date AS TIMESTAMP) comparison returns false instead of NULL"),
    28559: ("D-a", "wrong", "top-N window elimination returns the other join side for 0.0 / -0.0"),
    28562: ("D-a", "wrong", "contains(list, '') returns true for lists without an empty string"),
    28565: ("D-a", "error", "CASE guard around a STRUCT field cast ignored"),
    28566: ("D-a", "wrong", "both sides of a DOUBLE[] join key merged for [0.0] and [-0.0]"),
}


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120).stdout


def issue_state(n: int) -> dict:
    q = ('{repository(owner:"duckdb",name:"duckdb"){issue(number:%d){state stateReason createdAt author{login} '
         'labels(first:10){nodes{name}} timelineItems(itemTypes:[CLOSED_EVENT,CROSS_REFERENCED_EVENT],last:30){nodes{'
         '__typename ...on ClosedEvent{closer{__typename ...on PullRequest{number merged}}} '
         '...on CrossReferencedEvent{source{__typename ...on PullRequest{number state merged}}}}}}}}' % n)
    d = json.loads(gh("api", "graphql", "-f", f"query={q}"))["data"]["repository"]["issue"]
    prs, fixed_by = {}, ""
    for node in d["timelineItems"]["nodes"]:
        if node["__typename"] == "CrossReferencedEvent" and node["source"].get("number"):
            s = node["source"]
            prs[s["number"]] = "merged" if s["merged"] else s["state"].lower()
        if node["__typename"] == "ClosedEvent" and node.get("closer") and node["closer"].get("number"):
            fixed_by = str(node["closer"]["number"])
            prs[node["closer"]["number"]] = "merged" if node["closer"]["merged"] else "open"
    return {"state": d["state"].lower(), "reason": (d["stateReason"] or "").lower(), "filed": d["createdAt"][:10],
            "author": d["author"]["login"], "labels": ";".join(x["name"] for x in d["labels"]["nodes"]),
            "prs": ";".join(f"{k}:{v}" for k, v in sorted(prs.items())), "fixed_by": fixed_by}


UNIT = re.compile(r"\* (\S+ \S+) hunt_(\w+?)_(\w+)_r(\d)(_scale)?: quality=(.*?) valid=(\d+)% reach=(\S+) trees=(\d+) hits=(\d+)")


def hunt_units(run: str) -> list[dict]:
    rows = []
    for line in (WORK / "results" / "hunt" / run / "RUN_LOG.md").read_text().splitlines():
        m = UNIT.match(line)
        if m:
            rows.append({"run": run, "time": m[1], "target": m[3], "round": int(m[4]), "quality_ok": m[6] == "ok",
                         "valid_pct": int(m[7]), "reach": None if m[8] == "None" else float(m[8]),
                         "plan_trees": int(m[9]), "hits": int(m[10])})
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # 1. upstream issues
    rows = []
    for n, (method, kind, title) in ISSUES.items():
        s = issue_state(n)
        rows.append({"issue": n, "method": method, "kind": kind, "title": title, **s})
    with open(OUT / "issues.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    fixed = [r for r in rows if r["state"] == "closed" and r["reason"] == "completed"]
    open_fix = [r for r in rows if r["state"] == "open" and "open" in r["prs"]]
    summary = {"snapshot": now, "issues": len(rows),
               "by_method": {m: sum(r["method"] == m for r in rows) for m in ("A", "B", "C", "D-i", "D-a")},
               "by_kind": {k: sum(r["kind"] == k for r in rows) for k in ("wrong", "internal", "error")},
               "fixed_upstream": [r["issue"] for r in fixed],
               "open_with_fix_pr": [r["issue"] for r in open_fix],
               "reproduced_label": [r["issue"] for r in rows if "reproduced" in r["labels"]],
               "filed_by_team_member_niket": [r["issue"] for r in rows if r["author"] != "prabh1512"]}
    # 2. automated hunt runs
    units = hunt_units("hunt1") + hunt_units("smoke1")
    with open(OUT / "hunt_units.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(units[0]))
        w.writeheader()
        w.writerows(units)
    led = json.loads((WORK / "results" / "hunt" / "hunt1" / "ledger.json").read_text())
    h1 = [u for u in units if u["run"] == "hunt1"]
    summary["hunt1"] = {"llm_calls": led["llm_calls"], "targets": len(led["targets"]), "units": len(h1),
                        "units_quality_ok": sum(u["quality_ok"] for u in h1),
                        "units_with_hits": sum(u["hits"] > 0 for u in h1),
                        "median_valid_pct": sorted(u["valid_pct"] for u in h1)[len(h1) // 2],
                        "candidates": len(led["candidates"]),
                        "candidate_targets": sorted({c["target"] for c in led["candidates"]}),
                        "issues_filed": [r["issue"] for r in rows if r["method"] == "D-a" and r["issue"] != 28536]}
    # 3. controls
    ctl = {}
    for d in sorted((WORK / "results" / "targeted" / "controls").iterdir()):
        m = d / "metrics.json"
        if m.exists():
            j = json.loads(m.read_text())
            ctl[d.name] = {k: j.get(k) for k in ("quality", "reach_rate", "expr_reach_rate", "valid_rate", "hits_unique")}
    summary["controls"] = ctl
    # 4. Method C, Round 2 (teammate run, Gemini 2.5 Pro) and Method A, SQLsmith campaign
    fs = (REPO / "src" / "results" / "targeted" / "FINAL_SUMMARY.md").read_text()
    summary["round2_final_summary_valid_queries"] = re.search(r"Total Valid Queries Measured:\*\* ([\d,]+)", fs)[1]
    rep = (WORK / "results" / "campaign-sqlsmith" / "CAMPAIGN_REPORT.md").read_text()
    summary["sqlsmith"] = {k: re.search(rf"\*\*{k}\*\* \| ([\d,.]+)", rep)[1] for k in ("Queries Executed", "Semantically Valid", "Raw Anomalies Caught")}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))




def tables() -> None:
    """Write paper/tables/tab_issues.tex and tab_hunt1.tex from paper/data (no network)."""
    tab = REPO / "paper" / "tables"
    rows = list(csv.DictReader(open(OUT / "issues.csv")))
    manual_fix = {"27850": "28144"}  # closed by a maintainer by hand after PR #28144 was merged (issue timeline)
    kind = {"wrong": "wrong", "internal": "internal", "error": "spurious"}
    meth = {"B": "B", "C": "C", "D-i": "D-s", "D-a": "D-l"}
    lines = []
    for r in rows:
        prs = dict(x.split(":") for x in r["prs"].split(";") if x and not x.startswith("370:"))
        prs = {"28535": {"28540": "open"}, "28541": {"28549": "open"}}.get(r["issue"], prs)  # cross-refs that only mention the issue
        fix = r["fixed_by"] or manual_fix.get(r["issue"], "")
        if r["state"] == "closed":
            st = f"fixed, PR \\#{fix}"
        elif any(v == "open" for v in prs.values()):
            st = "open, PR " + ", ".join(f"\\#{k}" for k, v in prs.items() if v == "open")
        else:
            st = "open" + (", reproduced" if "reproduced" in r["labels"] else "")
        title = r["title"].replace("_", "\\_").replace("&", "\\&").replace("#", "\\#")
        lines.append(f"\\#{r['issue']} & {meth[r['method']]} & {kind[r['kind']]} & {title} & {st} \\\\")
    (tab / "tab_issues.tex").write_text(
        "%% generated by paper/scripts/collect_data.py from data/issues.csv\n"
        "\\begin{table}[t]\n\\caption{All 16 issues we filed on DuckDB. \\emph{Method}: the method of Section~\\ref{sec:approach} "
        "that found it (D-s: interactive session, D-l: unattended loop). \\emph{Kind}: wrong result (silent), internal error "
        "(an assertion or INTERNAL Error), or spurious error (a valid query fails). \\emph{Status} on " + json.loads((OUT / 'summary.json').read_text())['snapshot'][:10] +
        "; fix PRs are by maintainers or other contributors, except \\#28547 (ours).}\n\\label{tab:issues}\n\\scriptsize\\centering\n"
        "\\setlength{\\tabcolsep}{3pt}\n\\begin{tabular}{lllp{7.0cm}p{3.0cm}}\n\\toprule\nIssue & Meth. & Kind & What goes wrong & Status \\\\\n\\midrule\n" +
        "\n".join(lines) + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n")
    units = [u for u in csv.DictReader(open(OUT / "hunt_units.csv")) if u["run"] == "hunt1"]
    led = json.loads((WORK / "results" / "hunt" / "hunt1" / "ledger.json").read_text())
    issue_of = {"move_constants": "28541", "comparison_simplification": "28542", "topn_window_elimination": "28559",
                "empty_needle_removal": "28562", "remove_unused_columns": "28565, 28566"}  # registry known_issues.json
    order = []
    for u in units:
        if u["target"] not in order:
            order.append(u["target"])
    body = []
    for t in order:
        us = [u for u in units if u["target"] == t]
        reach = [float(u["reach"]) for u in us if u["reach"] not in ("", "None")]
        cands = sum(c["target"] == t for c in led["candidates"])
        body.append(f"\\texttt{{{t.replace('_', chr(92) + '_')}}} & {len(us)} & {sum(u['quality_ok'] == 'True' for u in us)} & "
                    f"{max(reach):.2f} & {sum(int(u['hits']) for u in us)} & {cands} & {issue_of.get(t, '--')} \\\\")
    tot_hits = sum(int(u["hits"]) for u in units)
    (tab / "tab_hunt1.tex").write_text(
        "%% generated by paper/scripts/collect_data.py from data/hunt_units.csv and the hunt1 ledger\n"
        "\\begin{table}[t]\n\\caption{The automated loop (Method D, run \\texttt{hunt1}): most targets give nothing, a few give bugs. "
        "\\emph{Units}: generator rounds run. \\emph{OK}: rounds the engine judge accepted. \\emph{Reach}: best fraction of valid "
        "queries whose plan changes when the target is switched off. \\emph{Hits}: raw differences before triage. "
        "\\emph{Cand.}: hits that survived triage. \\emph{Issues}: filed upstream.}\n\\label{tab:hunt1}\n\\footnotesize\\centering\n"
        "\\begin{tabular}{lrrrrrl}\n\\toprule\nTarget & Units & OK & Reach & Hits & Cand. & Issues \\\\\n\\midrule\n" +
        "\n".join(body) + f"\n\\midrule\nTotal & {len(units)} & {sum(u['quality_ok'] == 'True' for u in units)} & & {tot_hits} & "
        f"{len(led['candidates'])} & 6 \\\\\n\\bottomrule\n\\end{{tabular}}\n\\end{{table}}\n")


if __name__ == "__main__":
    if not os.environ.get("TABLES_ONLY"):
        main()
    tables()
