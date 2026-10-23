"""Fix-diff dossiers: turn upstream bug-fix commits made AFTER the 2.0.0 tag into variant-hunting targets for an LLM agent.

    python harness/fix_dossiers.py --refs origin/v2.0-cyanoptera --limit 60        # bugfix branch (what 2.0.1 will contain)
    python harness/fix_dossiers.py --refs origin/main --limit 150                  # also fixes on main since the tag

Why: a fix commit shows a bug that EXISTS in 2.0.0 (so it is known, never a new finding) and, more usefully, the bug
*pattern*. Big Sleep and Staczzer (directed patch testing: 2 incomplete fixes) found that LLMs are good at the next step:
"where else is the same mistake made?". Each dossier gives the agent the pre-fix code, the fix, and the reproducer from the
fix's own test, and asks for sibling sites + reproducers. Verify any reproducer with harness/verify_finding.py
(--dev-site newer snapshot): fails on 2.0.0 AND on the fixed snapshot => sibling / incomplete fix candidate; fails only on
2.0.0 => just the original bug.

Also writes known_fixed.json (PR, subject, files): an automatic "already known" list that replaces the hand-kept one.
Output: results/targeted/dossiers/<sha>.md and results/targeted/known_fixed.json
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

WORK = runner.WORK
OUT = WORK / "results" / "targeted" / "dossiers"
FIXY = re.compile(r"\b(fix|fixes|fixed|bug|crash|segfault|internal error|assert|incorrect|wrong|regression|overflow|leak|hang|deadlock)\b", re.I)


def git(*args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(WORK / "duckdb"), *args], capture_output=True, text=True, errors="replace", timeout=600)
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[-300:])
    return p.stdout


def test_sql(path: str, sha: str) -> str:
    """Reproducer statements from an added/changed sqllogictest file at that commit (statement / query blocks, no results)."""
    text = git("show", f"{sha}:{path}", check=False)
    blocks, cur = [], []
    for ln in text.splitlines():
        if ln.startswith(("statement", "query", "mode", "require", "load", "loop", "foreach", "endloop", "#", "----")):
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            if ln.startswith("----"):
                break_after = True
            continue
        if ln.strip() == "":
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            continue
        cur.append(ln)
    if cur:
        blocks.append("\n".join(cur))
    return "\n".join(b.rstrip(";") + ";" for b in blocks if re.match(r"(?i)\s*(select|with|insert|update|delete|create|merge|from|set|pragma|alter|drop|explain)", b))[:3500]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="v2.0.0")
    ap.add_argument("--refs", nargs="+", default=["origin/v2.0-cyanoptera"])
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--all", action="store_true", help="include commits whose subject does not look like a fix")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if not a.no_fetch:
        for ref in a.refs:
            remote, _, branch = ref.partition("/")
            subprocess.run(["git", "-C", str(WORK / "duckdb"), "fetch", "-q", remote, f"+refs/heads/{branch}:refs/remotes/{ref}"],
                           capture_output=True, text=True, timeout=1800)
    seen: set[str] = set()
    known: list[dict] = []
    made = 0
    for ref in a.refs:
        try:
            log = git("log", "--no-merges", "--format=%H%x09%ad%x09%s", "--date=short", f"{a.tag}..{ref}", "--", "src", "extension").splitlines()
        except RuntimeError as e:
            print(f"cannot read {ref}: {e}")
            continue
        for line in log:
            sha, date, subject = (line.split("\t", 2) + ["", ""])[:3]
            if sha in seen or not sha:
                continue
            seen.add(sha)
            files = git("show", "--name-only", "--format=", sha).split()
            tests = [f for f in files if f.startswith("test/") and f.endswith(".test")]
            src = [f for f in files if f.startswith(("src/", "extension/")) and not f.startswith("test/")]
            pr = (re.search(r"\(#(\d+)\)", subject) or re.search(r"#(\d+)", subject))
            known.append({"sha": sha[:10], "date": date, "subject": subject[:140], "pr": int(pr.group(1)) if pr else None,
                          "src_files": src[:8], "tests": tests[:5], "ref": ref})
            if made >= a.limit or not src:
                continue
            if not a.all and not (FIXY.search(subject) and tests):      # a fix with a reproducer test, or it is not worth a dossier
                continue
            diff = git("show", "--format=", "-U6", sha, "--", *src[:6])[:9000]
            repro = "\n\n".join(f"-- {t}\n{test_sql(t, sha)}" for t in tests[:2])
            body = [f"# Dossier {sha[:10]} - {subject}", "", f"* date {date}; branch {ref}; PR {('#' + str(pr.group(1))) if pr else 'unknown'}",
                    f"* source files changed: {', '.join(src[:8])}", f"* tests added/changed: {', '.join(tests[:5]) or 'none'}", "",
                    "## The fix (diff, pre-fix lines start with `-`)", "", "```diff", diff, "```", "",
                    "## Reproducer from the fix's own test", "", "```sql", repro or "-- (no sqllogictest in this commit; read the diff)", "```", "",
                    "## Your task (this bug is KNOWN and exists in 2.0.0 - do not report it)",
                    "1. State the bug pattern in one sentence (what assumption did the old code make?).",
                    "2. `grep -rn` the pattern in `$FUZZ_WORK/duckdb/src` and `extension/`: list other sites with the same assumption.",
                    "3. For each site, write a reproducer (or a generator, see docs/AGENT_BRIEF.md) and run it with "
                    "`verify_finding.py --dev-site $FUZZ_WORK/dev-snapshot`. Fails on 2.0.0 AND on the snapshot = sibling or incomplete fix.",
                    "4. Write what you tried and the result in AGENT_NOTES.md, including the negative results."]
            (OUT / f"{sha[:10]}.md").write_text("\n".join(body))
            made += 1
    (OUT.parent / "known_fixed.json").write_text(json.dumps(known, indent=1))
    print(f"{len(known)} commits since {a.tag} on {a.refs} touching src/extension; {made} dossiers -> {OUT}")
    print(f"known-fixed list: {OUT.parent / 'known_fixed.json'} ({sum(1 for k in known if k['pr'])} with PR numbers)")


if __name__ == "__main__":
    main()
