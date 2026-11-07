"""Automated rule-targeted bug hunt: an LLM writes generator programs from rule cards, the engine judges them, every hit is triaged.

    python harness/hunt_loop.py --run hunt1 --hours 8                       # rotate through the rule cards until the budget is spent
    python harness/hunt_loop.py --run smoke --hours 0.5 --targets timestamp_comparison --max-llm-calls 2 --cases 150

The method that found upstream 28531/28532/28534 (and the UBIGINT move_constants bug) on 2026-11-07, automated:
  target   rule cards (harness/rule_targets.py): churn, fix commits + their tests, preconditions, constant-building sites
  author   the LLM (default: `claude -p` headless, read-only tools on the DuckDB source only) writes ONE generator per round,
           with META["hypothesis"] and META["preconditions"]; it never sees the repo, the issue drafts, the controls or the
           quarantined session generators (its working directory is an empty folder)
  judge    gen_runner.run_generator: 5 configurations, rule-level reach, plan diversity; feedback goes into the next round
  triage   gen_triage on EVERY hit regardless of the quality verdict: verify, registry, minimize, main nightly, upstream search
  rotate   a target is retired after --rounds rounds (plus one scale run if a round was Quality ok) without a new candidate
Guards: one worker, nice, DuckDB memory_limit (gen_runner), a free-RAM check before every unit (pauses below --min-free-gb),
wall-clock and LLM-call budgets. Read-only GitHub calls only; nothing is ever filed or pushed.
Outputs: $FUZZ_WORK/results/hunt/<run>/ (RUN_LOG.md, ledger.json, CANDIDATES.md, llm/, gens/, triage/) and the generators in
$FUZZ_WORK/generators/hunt_<run>_<target>_r<k>.py (they count as LLM-written generators for the audit).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gen_runner  # noqa: E402
import gen_triage  # noqa: E402
import rule_targets  # noqa: E402
import runner  # noqa: E402

WORK = runner.WORK
CODE = HERE.parent
EXAMPLES = CODE / "gen" / "llm_generators"

METHOD = """You are the creative part of a differential fuzzer for the released DuckDB 2.0.0. Write ONE generator program (Python) aimed
at the target below. The engine runs every generated query in 5 configurations (default, the target switched OFF, threads=1,
PRAGMA enable_verification, delim_join_as_cte=false) and reports INTERNAL errors, crashes, results that differ between
configurations, and errors that only the default (optimized) configuration raises. Your goal is a NEW bug in the target code.

Method (this is what found real bugs in this code base - follow it):
1. READ the target's source file with your Read tool (the path is in the target card). List the preconditions under which the
   rewrite fires (every early return) and every place where it builds or converts a value (bounds, folded constants, casts).
2. Form a concrete HYPOTHESIS about what could be wrong, e.g. a constant computed at the edge of its type's range that no longer
   fits (c + 1 day past the largest timestamp, c2 - c1 overflowing, a cast to a narrower type), a cast that loses information
   (time of day, fractional digits), an operator/type combination the recent fix tests did not cover, NULL/empty/duplicate input.
3. Generate queries that SATISFY the preconditions (otherwise the rewrite never runs) and push exactly at the hypothesis.

Generator rules (the judge enforces these):
* Constants: take them from the rows you inserted (so = can match and </> sit on boundaries) and from EDGES, a dict available
  in your program: EDGES["UBIGINT"], EDGES["TIMESTAMP_MS"], EDGES["DATE"], EDGES["DOUBLE"], EDGES["DECIMAL(18,3)"], ... -> tuple of
  SQL literal strings at the limits of that type (min, max, neighbours, NaN/inf, BC dates, infinity, precision limits).
  Available keys: {edge_keys}.
* Put the target pattern in varied contexts: WHERE, SELECT list, CASE, aggregate FILTER, HAVING, JOIN ... ON, scalar subquery,
  NOT(...). A generator whose queries all look alike is rejected (plan-shape diversity), and so is one that rarely reaches the
  target (reach rate < 30%: for an expression rule, the rule's marker function must disappear from the plan when the rewriter runs).
* Results must be deterministic: no LIMIT/OFFSET without a full ORDER BY, no any_value/first/last/mode/list/string_agg/random/now,
  window ORDER BY keys must be unique (ties make row_number() legitimately nondeterministic), no files/extensions/network.
* Include NULLs, empty tables, duplicates and type-limit rows in the data; keep tables small (< 50 rows) unless the card says the
  optimizer only fires on large inputs (then use range(): CREATE TABLE f AS SELECT ... FROM range(20000) r(i)).
* Contract: a plain Python file; only `import random` (also itertools, string, math, textwrap, functools, collections); no other
  imports, no open/exec/eval/getattr/type, no dunder access. It must define
    META = {{"name": "{name}", "author_model": "{model}", "target": {target},
            "hypothesis": "<one paragraph: what in the code you expect to break and why>",
            "preconditions": "<the Apply()/CanOptimize() conditions your queries satisfy>",
            "description": "<one line>"}}
    def generate(rng):          # rng = random.Random(seed); pure and deterministic per seed; < 5 s per case
        return {{"setup": ["CREATE TABLE ...", "INSERT ..."], "queries": ["SELECT ...", ...]}}   # 2-6 queries per case
* Issues that are ALREADY KNOWN (do not aim at these exact shapes; neighbours of them are fine):
{known}

Output: ONLY the complete Python file in a single ```python fenced block. No explanation outside the block."""


def log(run_dir: Path, msg: str) -> None:
    line = f"* {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(run_dir / "RUN_LOG.md", "a") as fh:
        fh.write(line + "\n")


def free_gb() -> float:
    for ln in Path("/proc/meminfo").read_text().splitlines():
        if ln.startswith("MemAvailable:"):
            return int(ln.split()[1]) / 1024 / 1024
    return 99.0


def ram_guard(run_dir: Path, min_gb: float, max_wait_s: int = 1800) -> bool:
    waited = 0
    while free_gb() < min_gb:
        if waited == 0:
            log(run_dir, f"RAM guard: only {free_gb():.2f} GB available (< {min_gb}); pausing")
        if waited >= max_wait_s:
            log(run_dir, "RAM guard: still low after 30 min; stopping the run")
            return False
        time.sleep(60)
        waited += 60
    return True


def known_list() -> str:
    reg = json.loads((HERE / "known_issues.json").read_text())
    return "\n".join(f"  - {k['issue']}: {k['title'][:150]}" for k in reg)


def call_llm(prompt: str, cwd: Path, model: str, timeout: int, cmd_override: str = "") -> str:
    if cmd_override:                                           # e.g. a stub for tests: --llm-cmd "python stub.py"
        p = subprocess.run(cmd_override, shell=True, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.stdout
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    cmd = ["claude", "-p", "--model", model, "--output-format", "text", "--no-session-persistence", "--strict-mcp-config",
           "--allowedTools", "Read", "Grep", "Glob", "--disallowedTools", "Bash", "Edit", "Write", "WebFetch", "WebSearch", "NotebookEdit",
           "--add-dir", str(WORK / "duckdb")]
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"claude exited {p.returncode}: {p.stderr.strip()[-300:]}")
    return p.stdout


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, flags=re.S)
    return max(blocks, key=len) if blocks else text


class Hunt:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.dir = WORK / "results" / "hunt" / a.run
        for sub in ("llm", "gens", "triage"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        self.cwd = self.dir / "llm-cwd"                     # the LLM's working directory: examples only, nothing that leaks findings
        self.cwd.mkdir(exist_ok=True)
        for ex in ("example_join_trees.py", "example_grouping_sets.py"):
            if (EXAMPLES / ex).exists():
                shutil.copy(EXAMPLES / ex, self.cwd / ex)
        self.ledger_path = self.dir / "ledger.json"
        self.ledger = json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else {"targets": {}, "llm_calls": 0, "candidates": []}
        self.t0 = time.time()
        self.llm_failures = 0
        self.stop = False

    def save(self) -> None:
        self.ledger_path.write_text(json.dumps(self.ledger, indent=1))

    def left(self) -> float:
        return self.a.hours * 3600 - (time.time() - self.t0)

    def cards(self) -> list[dict]:
        idx = rule_targets.OUT / "index.json"
        if not idx.exists():
            rule_targets.build(self.a.since, 40)
        cards = json.loads(idx.read_text())
        if self.a.targets:
            want = set(self.a.targets.split(","))
            cards = [c for c in cards if c["name"] in want]
        return cards

    def next_target(self) -> dict | None:
        for c in self.cards():
            st = self.ledger["targets"].get(c["name"], {})
            if st.get("status") not in ("retired",):
                return c
        return None

    def prompt(self, card: dict, name: str, feedback: str) -> str:
        p = METHOD.format(edge_keys=", ".join(sorted(gen_runner_edges())), name=name, model=f"claude-{self.a.model}",
                          target=json.dumps(card["target"]), known=known_list())
        p += "\n\n## Target card\n\n" + (rule_targets.OUT / f"{card['name']}.md").read_text()
        p += ("\n\nExample generators (structure only, they target other features): " +
              ", ".join(f"./{f.name}" for f in sorted(self.cwd.glob("example_*.py"))))
        if feedback:
            p += "\n\n## Feedback on your previous generator for this target (engine-measured)\n\n" + feedback[:6000]
        return p

    def round(self, card: dict, k: int, feedback: str) -> tuple[str, dict | None]:
        name = f"hunt_{self.a.run}_{card['name']}_r{k}"
        prompt = self.prompt(card, name, feedback)
        (self.dir / "llm" / f"{name}.prompt.md").write_text(prompt)
        self.ledger["llm_calls"] += 1
        self.save()
        try:
            resp = call_llm(prompt, self.cwd, self.a.model, self.a.llm_timeout, self.a.llm_cmd)
        except Exception as e:                                # noqa: BLE001 - usage limit, network, timeout, sleep
            log(self.dir, f"{name}: LLM call failed: {str(e)[:200]}")
            self.ledger["llm_calls"] -= 1                     # a failed call is not a used call
            self.save()
            return name, {"llm_failed": True, "error": str(e)[:300]}
        if not resp.strip() or re.search(r"usage limit|rate limit|limit reached|quota|overloaded", resp[:400], re.I) and "```" not in resp:
            log(self.dir, f"{name}: LLM returned no usable answer: {resp.strip()[:200]!r}")
            self.ledger["llm_calls"] -= 1
            self.save()
            return name, {"llm_failed": True, "error": resp.strip()[:300]}
        (self.dir / "llm" / f"{name}.response.md").write_text(resp)
        code = extract_code(resp)
        bad = gen_runner.static_check(code)
        gpath = WORK / "generators" / f"{name}.py"
        if bad:
            log(self.dir, f"{name}: rejected by the static check: {bad[:3]}")
            return name, {"rejected": bad}
        gpath.write_text(code)
        if not ram_guard(self.dir, self.a.min_free_gb):
            return name, None
        m = gen_runner.run_generator(gpath, self.a.cases, 1, self.dir / "gens" / name, quiet=True)
        if not m.get("ok"):
            log(self.dir, f"{name}: generator failed: {m.get('rejected')}")
            return name, {"rejected": m.get("rejected")}
        log(self.dir, f"{name}: quality={m['quality']} valid={m['valid_rate']:.0%} reach={m.get('reach_rate')} trees={m['distinct_plan_signatures']} "
                      f"hits={m['hits_unique']} {m['hit_kinds']}")
        return name, m

    def triage(self, name: str, m: dict, card: dict) -> list[dict]:
        hits = self.dir / "gens" / name / "hits.jsonl"
        if not m.get("hits_unique"):
            return []
        if not ram_guard(self.dir, self.a.min_free_gb):
            return []
        res = gen_triage.triage_file(hits, self.dir / "triage" / name, card["target"].get("rule") or card["name"], search=not self.a.no_search,
                                     max_hits=self.a.max_triage)
        counts: dict[str, int] = {}
        for t in res:
            counts[t["cls"]] = counts.get(t["cls"], 0) + 1
        log(self.dir, f"{name}: triage {counts}")
        new = [t for t in res if t["cls"] in ("candidate_new", "sibling")]
        for t in new:
            self.ledger["candidates"].append({"target": card["name"], "generator": name, "cls": t["cls"], "kind": t["kind"],
                                              "signature": t.get("signature"), "main": t.get("main"), "minimized": t.get("minimized")})
            with open(self.dir / "CANDIDATES.md", "a") as fh:
                fh.write(f"\n## {t['cls']} - {card['name']} - {name}\n\n{gen_triage.render(t)}\n")
        self.save()
        return res

    def feedback(self, name: str, m: dict | None, triaged: list[dict]) -> str:
        if m is None:
            return "Your previous answer could not be used (no code block or the call failed). Output only the Python file."
        if m.get("rejected"):
            return "The previous generator was rejected before running: " + "; ".join(map(str, m["rejected"]))[:1500]
        fb = (self.dir / "gens" / name / "feedback.md").read_text() if (self.dir / "gens" / name / "feedback.md").exists() else ""
        if triaged:
            fb += "\n\n## Triage of its hits\n" + "\n".join(f"* {t['cls']}: {t['kind']} {str(t.get('signature', ''))[:120]}" for t in triaged[:10])
            fb += ("\n(rediscovery = a known issue; low_priority = the optimizer only hides an error; artifact/nondeterministic = "
                   "your queries were not deterministic or not equivalent - fix that first.) Move to a different hypothesis if the "
                   "hits are all known.")
        return fb

    def run(self) -> None:
        log(self.dir, f"start: {self.a.hours} h, model {self.a.model}, cases {self.a.cases}, rounds/target {self.a.rounds}, "
                      f"min free {self.a.min_free_gb} GB, {free_gb():.1f} GB available")
        while self.left() > 600 and self.ledger["llm_calls"] < self.a.max_llm_calls and not self.stop:
            card = self.next_target()
            if card is None:
                log(self.dir, "all targets retired")
                break
            st = self.ledger["targets"].setdefault(card["name"], {"rounds": 0, "status": "active", "best": None, "found": 0})
            feedback, any_ok = st.get("last_feedback", ""), False
            log(self.dir, f"target {card['name']} ({card['kind']}, switch {card['switch']}, score {card['score']})")
            while st["rounds"] < self.a.rounds and self.left() > 600 and self.ledger["llm_calls"] < self.a.max_llm_calls:
                k = st["rounds"] + 1                          # counted only once the round completes (a kill/sleep mid-round is retried)
                name, m = self.round(card, k, feedback)
                if m and m.get("llm_failed"):
                    # usage limit / network / sleep: do NOT count the round or retire the target; back off, then stop cleanly
                    self.llm_failures += 1
                    if self.llm_failures >= self.a.max_llm_failures:
                        log(self.dir, f"LLM unavailable {self.llm_failures} times in a row (usage limit?): stopping. Nothing is lost; "
                                      f"resume later with the same --run {self.a.run}")
                        self.stop = True
                        break
                    wait = min(1800, self.a.llm_backoff_s * self.llm_failures)
                    log(self.dir, f"LLM failure {self.llm_failures}/{self.a.max_llm_failures}: waiting {wait} s before retrying")
                    time.sleep(wait)
                    continue
                self.llm_failures = 0
                st["rounds"] = k
                triaged = self.triage(name, m, card) if m and m.get("ok") else []
                found = sum(1 for t in triaged if t["cls"] in ("candidate_new", "sibling"))
                st["found"] += found
                if m and m.get("quality") == "ok":
                    any_ok = True
                    st["best"] = name
                feedback = self.feedback(name, m, triaged)
                st["last_feedback"] = feedback[:6000]
                self.save()
                if found:
                    log(self.dir, f"{card['name']}: {found} candidate(s) -> CANDIDATES.md")
            if self.stop:
                self.save()
                break
            if any_ok and st.get("best") and self.left() > 1200 and not st.get("scaled"):
                g = WORK / "generators" / f"{st['best']}.py"
                if ram_guard(self.dir, self.a.min_free_gb):
                    m = gen_runner.run_generator(g, self.a.scale_cases, 2, self.dir / "gens" / f"{st['best']}_scale", quiet=True)
                    log(self.dir, f"{st['best']} scale run: {m.get('cases_run')} cases, hits={m.get('hits_unique')} {m.get('hit_kinds')}")
                    st["scaled"] = True
                    triaged = self.triage(f"{st['best']}_scale", m, card) if m.get("ok") else []
                    st["found"] += sum(1 for t in triaged if t["cls"] in ("candidate_new", "sibling"))
            if st["rounds"] >= self.a.rounds:
                st["status"] = "retired"
            self.save()
        log(self.dir, f"stop: {self.ledger['llm_calls']} LLM calls, {len(self.ledger['candidates'])} candidates, "
                      f"{(time.time() - self.t0) / 3600:.2f} h")


def gen_runner_edges() -> list[str]:
    import edge_values
    return list(edge_values.EDGES)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--targets", default="", help="comma-separated card names (default: all cards, highest score first)")
    ap.add_argument("--rounds", type=int, default=3, help="LLM write/judge/repair rounds per target")
    ap.add_argument("--cases", type=int, default=300)
    ap.add_argument("--scale-cases", type=int, default=1500)
    ap.add_argument("--model", default="opus")
    ap.add_argument("--max-llm-calls", type=int, default=60)
    ap.add_argument("--llm-timeout", type=int, default=900)
    ap.add_argument("--llm-cmd", default="", help="replace the claude call with a command reading the prompt on stdin (tests)")
    ap.add_argument("--min-free-gb", type=float, default=1.5)
    ap.add_argument("--llm-backoff-s", type=int, default=300)
    ap.add_argument("--max-llm-failures", type=int, default=4, help="consecutive failed LLM calls (usage limit, network) before stopping cleanly")
    ap.add_argument("--max-triage", type=int, default=10)
    ap.add_argument("--no-search", action="store_true")
    ap.add_argument("--since", default="2026-03-01")
    a = ap.parse_args()
    os.nice(10)
    Hunt(a).run()


if __name__ == "__main__":
    main()
