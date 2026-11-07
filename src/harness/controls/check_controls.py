"""End-to-end controls for rule-targeted generators (run after any change to gen_runner / verify_finding / known_issues.json).

    FUZZ_WORK=... PY src/harness/controls/check_controls.py [--n 300]

positive  control_date_trunc.py must be Quality: ok on RULE-level reach, produce wrong-result hits, and every hit must
          verify (verify_finding.verify_diff) as a deterministic wrong result classified as a REDISCOVERY of issue 28527.
negative  cheat_rule_reach.py names the same rule but never gives it a bare column: it must be rejected on rule reach
          even though its generic expression-level reach is high.
Prints CONTROLS PASSED / CONTROLS FAILED. Outputs go to $FUZZ_WORK/results/targeted/controls/ (never to gens/, which the
report and the audit read as LLM-written generators).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import gen_runner  # noqa: E402
import verify_finding  # noqa: E402

OUT = gen_runner.WORK / "results" / "targeted" / "controls"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-verify", type=int, default=6)
    a = ap.parse_args()
    fails: list[str] = []
    dev = gen_runner.WORK / "dev-snapshot"

    pos = gen_runner.run_generator(HERE / "control_date_trunc.py", a.n, a.seed, OUT / "control_date_trunc", quiet=True)
    print(f"positive: quality={pos.get('quality')} rule_reach={pos.get('reach_rate')} expr_reach={pos.get('expr_reach_rate')} "
          f"shapes={pos.get('distinct_plan_signatures')} hits={pos.get('hits_unique')}")
    if pos.get("quality") != "ok":
        fails.append(f"positive control not Quality ok: {pos.get('quality_flags')}")
    hits = [json.loads(x) for x in (OUT / "control_date_trunc" / "hits.jsonl").read_text().splitlines() if x.strip()] if pos.get("ok") else []
    diffs = [h for h in hits if h["kind"] == "config_diff"]
    if not diffs:
        fails.append("positive control produced no config_diff hit (issue 28527 not found)")
    for h in diffs[: a.max_verify]:
        v = verify_finding.verify_diff(h["script"], str(dev) if dev.exists() else "")
        rel = [(r["issue"], r["relation"]) for r in v.get("registry") or []]
        print(f"  verify {h['id']}: {v['verdict']} suspect={v.get('suspect')} registry={rel}")
        if v["verdict"] != "wrong_result" or (28527, "rediscovery") not in rel:
            fails.append(f"hit {h['id']} not classified as a rediscovery of 28527: {v['verdict']} {rel}")

    neg = gen_runner.run_generator(HERE / "cheat_rule_reach.py", a.n, a.seed, OUT / "cheat_rule_reach", quiet=True)
    print(f"negative: quality={neg.get('quality')} rule_reach={neg.get('reach_rate')} expr_reach={neg.get('expr_reach_rate')}")
    if neg.get("quality") == "ok":
        fails.append("cheat generator passed the judge: rule-level reach does not stop constant-folding / non-column calls")
    if not any("reach" in f for f in neg.get("quality_flags", [])):
        fails.append(f"cheat generator rejected, but not on reach: {neg.get('quality_flags')}")

    print("CONTROLS FAILED\n" + "\n".join("[FAIL] " + f for f in fails) if fails else "CONTROLS PASSED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
