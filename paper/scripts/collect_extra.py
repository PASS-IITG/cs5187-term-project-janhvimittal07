"""Triage-class totals of hunt1 and control rediscoveries -> paper/data/extra.json (run after collect_data.py)."""
import ast, collections, json, os, re
from pathlib import Path
WORK = Path(os.environ.get("FUZZ_WORK", Path.home() / "cs5187")); OUT = Path(__file__).resolve().parents[1] / "data"
c = collections.Counter()
for line in (WORK / "results/hunt/hunt1/RUN_LOG.md").read_text().splitlines():
    m = re.search(r"triage (\{.*\})", line)
    if m:
        c.update(ast.literal_eval(m[1]))
ctl = {}
for d in sorted((WORK / "results/targeted/controls").glob("control_rule_*")):
    rs = [json.loads(x) for f in d.glob("triage*/**/*.jsonl") for x in open(f) if x.strip()] + \
         [json.loads(x) for f in d.glob("triage*.jsonl") for x in open(f) if x.strip()]
    ctl[d.name] = sorted({str(r["issue"]) for t in rs if t.get("cls") == "rediscovery" for r in t.get("registry") or [] if r.get("relation") == "rediscovery"})
out = {"hunt1_triage_classes": dict(c), "hunt1_triaged_hits": sum(c.values()), "control_rediscoveries": ctl}
(OUT / "extra.json").write_text(json.dumps(out, indent=1)); print(json.dumps(out))
