"""One-command, time-boxed, resumable targeted bug hunt on the released DuckDB 2.0.0 (built for unattended / AI-agent use).

    python harness/targeted_run.py --hours 12                # the real run
    python harness/targeted_run.py --smoke                   # ~15 minute end-to-end self-test of every phase

What it does, in order (see docs/RUNBOOK_TARGETED_RUN.md for the reasoning):
  0 preflight   version gate: refuses to start unless the wheel, source tree and ASan build are the released 2.0.0
  1 pack        builds the new-2.0 seed set, new-code hints and feature cards if missing
  2 trig        trigger fuzzer (new feature; emulation / persistence / INTERNAL oracles)       } wheel-based, light
  3 gen         bottom-up + window generators (delim_join_as_cte on/off comparison)             }
  4 join        JoinEquiv join oracle                                                           }
  5 sweep       differential + NoREC/TLP sweep over the tests ADDED in 2.0
  6 asan        ASan+UBSan replay of the added tests, then of 6000 mutants of them                one ASan job at a time
  7 llm         only if LLM_BASE_URL and LLM_MODEL are set: LLM generation aimed at new code, replayed on ASan
  8 report      every hit is turned into a standalone script, replayed ALONE on all builds/settings
                (harness/verify_finding.py), de-duplicated, searched in upstream issues + PRs, and classified.

Output (results/targeted/): RUN_LOG.md (every command, duration, rc, summary), state.json (resume), logs/, hits/,
candidates/, RUN_REPORT.md. It never pushes to git, never posts to GitHub, and never changes anything outside $FUZZ_WORK.
Resume after an interruption by running the same command again: completed units are skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE.parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402

WORK = runner.WORK
OUT = WORK / "results" / "targeted"
PY = sys.executable
SCRATCH = WORK / "work-scratch"

ENV = dict(os.environ)
for _v in ("AI_AGENT", "CLAUDECODE", "CLAUDE_CODE", "CURSOR_AGENT"):
    ENV.pop(_v, None)                                     # the DuckDB shell prints JSON errors in "agent mode"
ENV["FUZZ_WORK"] = str(WORK)
ENV["ASAN_OPTIONS"] = "detect_leaks=0:symbolize=1:abort_on_error=0:hard_rss_limit_mb=2500:allocator_may_return_null=1"
ENV["UBSAN_OPTIONS"] = "print_stacktrace=1:halt_on_error=1"
HEAVY = ("asan_replay.py",)                                # run under nice and (when available) a memory cgroup


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Run:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.t0 = time.time()
        self.deadline = self.t0 + a.hours * 3600
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "logs").mkdir(exist_ok=True)
        (OUT / "hits").mkdir(exist_ok=True)
        SCRATCH.mkdir(exist_ok=True)
        self.state_path = OUT / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() and not a.fresh else {"done": {}}
        self.log_path = OUT / "RUN_LOG.md"
        if not self.log_path.exists() or a.fresh:
            self.log_path.write_text(f"# Run log (started {now()}, budget {a.hours} h{', SMOKE' if a.smoke else ''})\n\n"
                                     "| time | unit | rc | seconds | summary |\n|---|---|---|---|---|\n")

    # -------------------------------------------------------------- bookkeeping
    def left(self) -> float:
        return self.deadline - time.time()

    def save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=1))

    def log(self, unit: str, rc: int, secs: float, summary: str) -> None:
        with open(self.log_path, "a") as fh:
            fh.write(f"| {now()} | {unit} | {rc} | {secs:.0f} | {summary.replace('|', '/')[:200]} |\n")
        print(f"[{now()}] {unit}: rc={rc} {secs:.0f}s  {summary[:140]}", flush=True)

    def unit(self, name: str, cmd: list[str], timeout: float, hits: list[Path] | None = None, copy_to: str = "") -> bool:
        """Run one unit unless already done. Returns True if it ran (or was done) cleanly enough to continue."""
        if self.state["done"].get(name):
            return True
        timeout = max(30.0, min(timeout, self.left() - 60))
        full = [PY if c == "PY" else c for c in cmd]
        if Path(full[1]).name in HEAVY if len(full) > 1 else False:
            full = ["nice", "-n", "10"] + full
            if shutil.which("systemd-run"):
                full = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryHigh=3G", "-p", "MemoryMax=4G"] + full
        elif full[0] == PY:
            full = ["nice", "-n", "10"] + full
        logf = OUT / "logs" / f"{name}.log"
        t = time.time()
        try:
            with open(logf, "w") as fh:
                p = subprocess.Popen(full, cwd=SCRATCH, env=ENV, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    rc = p.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGTERM)
                    time.sleep(5)
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    rc = -15
        except Exception as e:                                     # noqa: BLE001
            self.log(name, -1, time.time() - t, f"could not start: {e}")
            return False
        tail = [l for l in logf.read_text(errors="replace").splitlines() if l.strip()][-1:] or [""]
        for h in hits or []:
            if h.exists():
                dst = OUT / "hits" / f"{name}__{h.name}"
                shutil.copy(h, dst)
        self.log(name, rc, time.time() - t, tail[0])
        if rc in (0, -15):                                         # -15: time box reached; the partial output counts
            self.state["done"][name] = {"rc": rc, "at": now()}
            self.save()
        return rc in (0, -15)

    def slice_end(self, frac_start: float, frac_end: float) -> float:
        usable = (self.deadline - self.t0) - self.a.reserve_min * 60
        return self.t0 + usable * frac_end

    # -------------------------------------------------------------- phases
    def preflight(self) -> None:
        p = subprocess.run([PY, str(HERE / "preflight.py")], capture_output=True, text=True, env=ENV)
        (OUT / "logs" / "preflight.log").write_text(p.stdout + p.stderr)
        print(p.stdout)
        self.log("preflight", p.returncode, 0, (p.stdout.strip().splitlines() or [""])[-1])
        if p.returncode != 0:
            sys.exit("preflight FAILED: fix the setup (see messages above) before running. Nothing was run.")

    def pack(self) -> None:
        if not (WORK / "corpus" / "seeds-v200-added").exists():
            self.unit("pack_seeds", [PY, str(HERE / "make_added_seeds.py")], 1200)
        hn = WORK / "results" / "hints-v200-new.json"
        if not hn.exists() and self.a.llm:
            try:
                import tree_sitter_cpp  # noqa: F401
                self.unit("pack_hints", [PY, str(CODE / "static" / "extract_hints.py"), "--out", str(WORK / "results" / "hints-v200.json"),
                                         "--subsystems", "src/optimizer", "src/planner", "src/execution", "src/function", "src/storage", "src/parser",
                                         "src/catalog", "src/main", "src/common", "extension/core_functions", "extension/json", "extension/parquet"], 900)
                self.unit("pack_newhints", [PY, str(CODE / "static" / "new_code_hints.py"), "--hints", str(WORK / "results" / "hints-v200.json"), "--out", str(hn)], 600)
            except ImportError:
                self.log("pack_hints", 1, 0, "tree-sitter missing: pip install tree-sitter tree-sitter-cpp (only needed for the LLM phase)")
        if self.a.llm:
            self.unit("pack_cards", [PY, str(CODE / "llm" / "feature_cards.py"), "build"], 300)

    def loop_seeds(self, prefix: str, make_cmd, per_unit_min: float, hits_for, until: float) -> None:
        seed = self.a.seed_base
        while time.time() + per_unit_min * 60 < until and self.left() > self.a.reserve_min * 60 + per_unit_min * 60:
            name = f"{prefix}_s{seed}"
            if not self.state["done"].get(name):
                if not self.unit(name, make_cmd(seed), per_unit_min * 60 * 2, hits_for(seed)):
                    break
            seed += 1
            if self.a.smoke:
                break

    def run_all(self) -> None:
        a = self.a
        s = a.smoke
        # fractions of the usable time per phase (cumulative end points)
        trig_n, bu_n, join_n = (60, 150, 80) if s else (3000, 8000, 10000)
        self.pack()
        added = WORK / "corpus" / "seeds-v200-added"
        # 2 trig
        self.loop_seeds("trig", lambda sd: [PY, str(HERE / "fuzz_triggers.py"), "--n", str(trig_n), "--seed", str(sd)], 1 if s else 12,
                        lambda sd: [WORK / "results" / "triggers" / f"hits-seed{sd}.jsonl"], self.slice_end(0, 0.25 if a.llm else 0.35))
        # 3 gen
        for tool, nm in (("bottomup.py", "bu"), ("fuzz_windows.py", "win")):
            hf = (lambda sd, tool=tool: [WORK / "results" / ("bottomup" if tool == "bottomup.py" else "windows") /
                                         ("hits.jsonl" if tool == "bottomup.py" else f"hits-seed{sd}.jsonl")])
            self.loop_seeds(nm, lambda sd, tool=tool: [PY, str(HERE / tool), "--n", str(bu_n), "--seed", str(sd)], 1 if s else 4, hf,
                            self.slice_end(0, 0.31 if nm == "bu" else 0.40) if not a.llm else self.slice_end(0, 0.31 if nm == "bu" else 0.37))
        # 4 join
        self.loop_seeds("join", lambda sd: [PY, str(HERE / "fuzz_joins.py"), "--n", str(join_n), "--seed", str(sd)], 1 if s else 25,
                        lambda sd: [WORK / "results" / "joins" / f"hits-seed{sd}.jsonl"], self.slice_end(0, 0.50 if not a.llm else 0.45))
        # 5 sweep
        if added.exists():
            sw = OUT / "sweep-added.jsonl"
            self.unit("sweep_added", [PY, str(HERE / "sweep_oracle.py"), "--seeds", str(added), "--out", str(sw), "--metamorphic", "--workers", "2"]
                      + (["--limit", "30"] if s else []), 3600, [sw])
        # 6 asan
        if added.exists():
            self.unit("asan_added", [PY, str(HERE / "asan_replay.py"), "--sql-dir", str(added), "--out", str(OUT / "asan-added")]
                      + (["--limit", "200"] if s else []), 7200, [OUT / "asan-added" / "hits.jsonl"])
            mut = WORK / "corpus" / "mutants" / "targeted"
            self.unit("mutate", [PY, str(HERE / "mutate_corpus.py"), "--seeds", str(added), "--n", "150" if s else "6000", "--out", str(mut), "--seed", "1"], 900)
            self.unit("asan_mutants", [PY, str(HERE / "asan_replay.py"), "--sql-dir", str(mut), "--out", str(OUT / "asan-mutants")]
                      + (["--limit", "200"] if s else []), max(600.0, self.slice_end(0, 0.85) - time.time()), [OUT / "asan-mutants" / "hits.jsonl"])
        # 7 llm
        if a.llm:
            if os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_MODEL"):
                cmd = [PY, str(CODE / "llm" / "generate.py"), "--run", "targeted", "--targets", "hints",
                       "--hints", str(WORK / "results" / "hints-v200-new.json"), "--cards", str(WORK / "results" / "feature-cards.json"),
                       "--n-prompts", "10" if s else "300"]
                if self.unit("llm_generate", cmd, max(300.0, self.slice_end(0, 0.97) - time.time())):
                    self.unit("asan_llm", [PY, str(HERE / "asan_replay.py"), "--sql-dir", str(WORK / "corpus" / "llm" / "targeted"),
                                           "--out", str(OUT / "asan-llm")], 3600, [OUT / "asan-llm" / "hits.jsonl"])
            else:
                self.log("llm", 0, 0, "skipped: LLM_BASE_URL / LLM_MODEL not set")
        self.report()

    # -------------------------------------------------------------- report
    def candidates(self) -> list[dict]:
        """Turn every tool's hit records into standalone scripts."""
        import bottomup, fuzz_windows  # noqa: E402
        cands: list[dict] = []

        def add(tool: str, hid: str, kind: str, detail: str, script: str, review: bool = False) -> None:
            cands.append({"tool": tool, "id": hid, "kind": kind, "detail": detail[:200], "script": script, "review": review})

        for f in sorted((OUT / "hits").glob("*.jsonl")):
            unit = f.name.split("__")[0]
            for line in f.read_text().splitlines():
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                kind = o.get("kind", o.get("outcome", ""))
                review = kind in ("value_diff", "error_diff", "persist_mismatch", "emu_mismatch", "delimcte_diff", "delimcte_error_diff",
                                  "join_mismatch", "vector_diff", "value", "norec", "tlp", "timeout")
                det = str(o.get("detail", o.get("evidence", "")))
                if unit.startswith("trig") and o.get("case"):
                    c = o["case"]
                    sc = ";\n".join(c["schema"]) + ";\n" + ";\n".join(t["sql"] for t in c["triggers"]) + ";\n" + ";\n".join(s["sql"] for s in c["steps"]) + ";\n"
                    add(unit, f"case{o['id']}", kind, det, ("PRAGMA enable_verification;\n" if o.get("verify") else "") + sc, review)
                elif unit.startswith("bu") and o.get("sql"):
                    add(unit, f"q{o['id']}", kind, det, ";\n".join(bottomup.SETUP) + ";\n" + o["sql"] + ";\n", review)
                elif unit.startswith("win") and o.get("sql"):
                    add(unit, f"q{o['id']}", kind, det, ("PRAGMA enable_verification;\n" if o.get("mode") == "verify" else "") +
                        ";\n".join(fuzz_windows.SETUP) + ";\n" + o["sql"] + ";\n", review)
                elif unit.startswith("join") and o.get("setup"):
                    add(unit, f"case{o['id']}", kind, det, ";\n".join(o["setup"]) + ";\n" + str(o.get(o.get("variant"), o.get("q"))) + ";\n", review)
                elif unit.startswith("asan") and o.get("sql"):
                    if o.get("verdict", "").startswith("reproduces"):
                        add(unit, o["id"], kind, det, o["sql"], False)
                elif unit.startswith("sweep"):
                    for fnd in o.get("found", []):
                        add(unit, f"{o.get('seed')}#{fnd.get('idx')}", fnd["kind"], str(fnd.get("detail"))[:200], str(fnd.get("sql")), True)
        return cands

    def report(self) -> None:
        import verify_finding
        self.log("report", 0, 0, "collecting candidates")
        cands = self.candidates()
        auto = [c for c in cands if not c["review"]]
        review = [c for c in cands if c["review"]]
        cdir = OUT / "candidates"
        cdir.mkdir(exist_ok=True)
        # de-duplicate by (tool family, kind, normalised detail)
        groups: dict[str, list[dict]] = defaultdict(list)
        for c in auto:
            groups[f"{c['tool'].split('_')[0]}|{c['kind']}|{verify_finding.normalise(c['detail'])[:90]}"].append(c)
        dev = WORK / "dev-snapshot"
        if not dev.exists():
            subprocess.run([PY, "-m", "pip", "install", "-q", "--pre", "--no-deps", "--target", str(dev), "duckdb"], capture_output=True, timeout=600, env=ENV)
        verified: list[dict] = []
        for i, (key, items) in enumerate(sorted(groups.items())[: self.a.max_verify]):
            c = items[0]
            f = cdir / f"cand{i:03d}.sql"
            f.write_text(c["script"])
            try:
                v = verify_finding.verify(c["script"], str(dev) if dev.exists() else "", search=False)
            except Exception as e:                                   # noqa: BLE001
                v = {"verdict": "verify_error", "signature": str(e)[:100], "fixed_in_dev": False, "example_message": ""}
            v.update(file=str(f), tool=c["tool"], id=c["id"], kind=c["kind"], count=len(items), detail=c["detail"])
            if v["verdict"] in ("reproduces", "asan_only") and not v.get("fixed_in_dev"):
                v["search_terms"], v["upstream_hits"] = verify_finding.upstream_check(v.get("example_message", ""), v.get("frames", []))
            verified.append(v)
            print(f"[verify {i}] {v['verdict']:16} x{len(items):<3} {c['tool']:14} {v.get('signature', '')[:70]}", flush=True)
        # classify
        new, known, fixed, artifact, unsearched = [], [], [], [], []
        for v in verified:
            if v["verdict"] == "not_reproducible" or v["verdict"] == "verify_error":
                artifact.append(v)
            elif v.get("fixed_in_dev"):
                fixed.append(v)
            elif v.get("upstream_hits"):
                known.append(v)
            elif not v.get("search_terms"):
                unsearched.append(v)           # no error text / usable stack frames: it could be known, so it is NOT "new"
            else:
                new.append(v)
        L = [f"# RUN_REPORT - DuckDB 2.0.0 targeted run - {now()}", "",
             f"Budget {self.a.hours} h{' (SMOKE TEST - not a real run)' if self.a.smoke else ''}; wall time used {(time.time() - self.t0) / 3600:.2f} h.",
             "", "## Version", "", "```", subprocess.run([PY, str(HERE / "preflight.py")], capture_output=True, text=True, env=ENV).stdout.strip(), "```", "",
             "## Units run", "", (self.log_path.read_text()), "",
             "## Summary", "",
             f"* hits collected: {len(cands)} ({len(auto)} auto-verifiable, {len(review)} need human review)",
             f"* distinct auto-verifiable groups verified: {len(verified)}",
             f"* **CANDIDATE NEW (reproduces alone, not fixed in the newer snapshot, nothing found upstream): {len(new)}**",
             f"* reproduces but matches an upstream issue/PR (probably known): {len(known)}",
             f"* reproduces but could NOT be searched upstream (no error text or stack frames): {len(unsearched)}  <- a human must search by hand",
             f"* already fixed in the development snapshot: {len(fixed)}",
             f"* not reproducible alone / verification error (artifacts): {len(artifact)}", ""]
        if not new:
            L += ["**No candidate new bug.** This is the expected outcome of most runs. Do not report anything as a bug.", ""]

        def table(title: str, items: list[dict], hits: bool = False) -> None:
            if not items:
                return
            L.extend([f"## {title}", ""])
            for v in items:
                L.append(f"* `{v['file']}` ({v['tool']}, {v['kind']}, x{v['count']}): **{v['verdict']}** - {v.get('example_message') or v['detail']}")
                L.append(f"  * depends on: {', '.join(k for k, x in v.get('depends_on', {}).items() if x) or 'nothing special'}; "
                         f"runs: {', '.join(f'{k}={r['status']}' for k, r in v.get('runs', {}).items())}; searched: {v.get('search_terms') or 'nothing'}")
                for h in (v.get("upstream_hits") or [])[:5]:
                    L.append(f"  * upstream: {h['kind']} #{h['number']} ({h['state']}, {h['updated']}): {h['title']}")
            L.append("")

        table("CANDIDATE NEW - a human must still read the script, reduce it, and search upstream by hand", new)
        table("Reproduces, but upstream has matching reports (check each)", known)
        table("Reproduces but not searchable automatically - search upstream by hand before believing it is new", unsearched)
        table("Already fixed in the development snapshot", fixed)
        if review:
            L.extend(["## Needs human review (semantic differences: result mismatches, error differences, timeouts)", "",
                      "These are *not* verified bugs. Many are by design (STATS(), settings, ordering). First 40:", ""])
            for c in review[:40]:
                L.append(f"* {c['tool']} {c['id']} [{c['kind']}] {c['detail'][:140]}")
            L.append("")
        table("Artifacts (did not reproduce alone)", artifact)
        L += ["## Honesty notes", "",
              "* Nothing here was filed or pushed. Every 'candidate' still needs a human to read the SQL and search upstream.",
              "* Counts are real counts from this run; do not round up in the paper.", ""]
        (OUT / "RUN_REPORT.md").write_text("\n".join(L))
        self.log("report", 0, 0, f"{len(new)} candidate new, {len(known)} known?, {len(unsearched)} unsearched, {len(fixed)} fixed, {len(artifact)} artifacts -> RUN_REPORT.md")
        print(f"\nDONE. Report: {OUT / 'RUN_REPORT.md'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--smoke", action="store_true", help="tiny end-to-end self-test (~15 min); sets --hours 0.5")
    ap.add_argument("--fresh", action="store_true", help="ignore state.json and start over")
    ap.add_argument("--llm", action="store_true", help="also run the LLM phase (needs LLM_BASE_URL and LLM_MODEL)")
    ap.add_argument("--seed-base", type=int, default=100)
    ap.add_argument("--reserve-min", type=float, default=25.0, help="minutes kept for triage and the report")
    ap.add_argument("--max-verify", type=int, default=60)
    a = ap.parse_args()
    if a.smoke:
        a.hours = max(a.hours, 0.5) if a.hours != 12.0 else 0.5
        a.reserve_min = 8
    r = Run(a)
    r.preflight()
    r.run_all()


if __name__ == "__main__":
    main()
