"""End-to-end pipeline: seeds -> sweeps -> triage -> upstream dedupe -> REPORT.md

    python harness/fuzz.py all                    # everything below, in order
    python harness/fuzz.py all --baseline-minutes 30
    python harness/fuzz.py sweep | triage | report | status

Stages
  seeds     extract DuckDB sqllogictests into corpus/seeds (skipped if present)
  sweep     crash sweep on the ASan build + differential sweep on stock build
  baseline  (optional) mutation fuzzing on the ASan build for N minutes
  triage    reproduce, minimise, confirm on stock build, search upstream
  report    results/REPORT.md  (new vs likely-duplicate findings, with repros)
Everything is incremental: findings already in results/known.json are skipped.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402
import sweep_oracle  # noqa: E402
import triage  # noqa: E402

WORK = runner.WORK
RES = WORK / "results"
KNOWN = RES / "known.json"


def sh(cmd: list[str], **kw) -> int:
    print("+", " ".join(map(str, cmd)), flush=True)
    return subprocess.run(cmd, **kw).returncode


def load_known() -> dict:
    return json.loads(KNOWN.read_text()) if KNOWN.exists() else {}


def stage_seeds() -> None:
    if (WORK / "corpus" / "seeds").exists() and any((WORK / "corpus" / "seeds").glob("*.sql")):
        return
    sh([sys.executable, HERE / "extract_seeds.py"])


def stage_sweep(workers: int, limit: int) -> None:
    lim = ["--limit", str(limit)] if limit else []
    sh([sys.executable, HERE / "sweep_seeds.py", "--build", "asan", "--workers", str(workers), *lim])
    sh([sys.executable, HERE / "sweep_oracle.py", "--workers", str(workers), *lim])


def stage_baseline(minutes: float, seed: int) -> None:
    if minutes > 0:
        sh([sys.executable, HERE / "baseline.py", "--build", "asan", "--minutes", str(minutes),
            "--seed", str(seed)])


def candidates():
    """Yield (kind, origin, payload) from every sweep output."""
    seeds_dir = WORK / "corpus" / "seeds"
    p = RES / "sweep-asan.jsonl"
    if p.exists():
        for ln in p.read_text().splitlines():
            r = json.loads(ln)
            if r["outcome"] in triage.CRASHY:
                stmts = runner.split_statements((seeds_dir / r["seed"]).read_text(errors="replace"))
                yield "crash", f"sweep-asan:{r['seed']}", {"stmts": stmts}
    p = RES / "sweep-oracle.jsonl"
    if p.exists():
        for ln in p.read_text().splitlines():
            r = json.loads(ln)
            f = seeds_dir / r["seed"]
            if not f.exists():
                continue
            stmts = runner.split_statements(f.read_text(errors="replace"))
            if "died" in r:
                yield "crash", f"oracle-died:{r['seed']}", {"stmts": stmts}
            for x in r.get("found", []):
                if sweep_oracle.is_noise(r["seed"], x["sql"]) and x["kind"] != "internal":
                    continue
                if x["kind"] == "internal":
                    yield "crash", f"oracle-internal:{r['seed']}#{x['idx']}", {"stmts": stmts[:x["idx"] + 1]}
                else:
                    yield x["kind"], f"oracle-{x['kind']}:{r['seed']}#{x['idx']}", {
                        "setup": stmts[:x["idx"]], "query": stmts[x["idx"]], "differs": x["differs"]}
    crashes = WORK / "crashes"
    if crashes.exists():
        for f in sorted(crashes.glob("*.sql")):
            yield "crash", f"baseline:{f.name}", {"stmts": runner.split_statements(f.read_text())}


def stage_triage(max_items: int) -> None:
    known = load_known()
    done = 0
    for kind, origin, pl in candidates():
        if origin in known.get("_origins", []):
            continue
        if done >= max_items:
            break
        t0 = time.time()
        try:
            f = (triage.triage_crash(pl["stmts"], origin) if kind == "crash" else
                 triage.triage_value(pl["setup"], pl["query"], kind, pl["differs"], origin))
        except Exception as e:                       # noqa: BLE001
            print(f"  triage error {origin}: {e!r}", flush=True)
            f = None
        known.setdefault("_origins", []).append(origin)
        done += 1
        if f is None:
            print(f"[-] {origin}: not reproducible ({time.time()-t0:.0f}s)", flush=True)
        else:
            new = f["id"] not in known
            known[f["id"]] = {"verdict": f["upstream"]["verdict"], "origin": origin, "kind": f["kind"]}
            print(f"[{'NEW' if new else 'dup'}] {f['id']} {f['kind']} {f['upstream']['verdict']} "
                  f"cfg={f['config']} opts={f['disabled_optimizers']} stmts={f['n_statements']} "
                  f"({time.time()-t0:.0f}s)", flush=True)
        KNOWN.write_text(json.dumps(known, indent=1))


def stage_report() -> Path:
    items = []
    for m in sorted(triage.OUT.glob("*/meta.json")):
        items.append(json.loads(m.read_text()))

    order = {"no-match": 0, "similar-found": 1, "duplicate-likely": 2}
    items.sort(key=lambda f: (
        order.get(f.get("upstream", {}).get("verdict", "untriaged"), 3),
        f.get("kind", "")
    ))

    lines = [
        f"# Fuzzing report — {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"{len(items)} saved findings; findings without upstream metadata are marked untriaged.",
        "Automatic upstream matches require manual confirmation.",
        "",
        "| id | kind | config | optimizers | stmts | stock build | main build | upstream |",
        "|---|---|---|---|---|---|---|---|"
    ]

    for f in items:
        upstream = f.get("upstream") or {}
        st = f.get("stock_build") or {}
        mb = f.get("main_build") or {}
        lines.append(
            f"| {f.get('id', '-')} | {f.get('kind', '-')}/{f.get('outcome', '-')} | "
            f"{f.get('config', '-')} | "
            f"{','.join(f.get('disabled_optimizers') or []) or '-'} | "
            f"{f.get('n_statements', '-')} | "
            f"{'yes' if st.get('reproduces') else 'no' if st else 'unknown'} | "
            f"{'yes' if mb.get('reproduces_on_main') else 'no' if mb.get('available') else 'n/a'} | "
            f"{upstream.get('verdict', 'untriaged')} |"
        )

    for f in items:
        upstream = f.get("upstream") or {}
        disabled = f.get("disabled_optimizers") or []
        config = f.get("config", "")
        setup = triage.disable(disabled) if disabled else triage.CFG.get(config, [])
        lines += [
            "",
            f"## {f.get('id', '-')} — {f.get('kind', '-')} "
            f"({upstream.get('verdict', 'untriaged')})",
            "",
            f"Origin: `{f.get('origin', '-')}`",
            f"Outcome: `{f.get('outcome', '-')}`; signature `{f.get('signature', '-')}`",
            f"Config: `{config}`; disabled optimizers: `{','.join(disabled) or '-'}`",
        ]
        # N2: show pinned-main result
        mb = f.get("main_build") or {}
        if mb.get("available"):
            lines.append(
                f"Pinned-main ({triage.MAIN_BIN.name}): outcome=`{mb.get('outcome', '-')}` "
                f"reproduces={mb.get('reproduces_on_main', '-')} ({mb.get('duration_s', '-')}s)"
            )
        lines += [
            "",
            "```sql",
            triage.prelude(setup) + "\n".join(f.get("minimal_sql") or []),
            "```",
            ""
        ]

        if f.get("error_core"):
            lines.append(f"Error: `{' '.join(f['error_core'])}`")

        for m in upstream.get("matches", [])[:5]:
            lines.append(
                f"- {'**' if m.get('strong') else ''}#{m.get('number', '?')} "
                f"[{m.get('kind', '?')}/{m.get('state', '?')}] "
                f"{m.get('title', '')}{'**' if m.get('strong') else ''} — {m.get('url', '')}"
            )

    out = RES / "REPORT.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"report: {out}", flush=True)
    return out

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["all", "seeds", "sweep", "baseline", "triage", "report", "status"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="limit number of seeds (testing)")
    ap.add_argument("--baseline-minutes", type=float, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-triage", type=int, default=200)
    a = ap.parse_args()
    RES.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if a.stage in ("all", "seeds"):
        stage_seeds()
    if a.stage in ("all", "sweep"):
        stage_sweep(a.workers, a.limit)
    if a.stage in ("all", "baseline"):
        stage_baseline(a.baseline_minutes, a.seed)
    if a.stage in ("all", "triage"):
        stage_triage(a.max_triage)
    if a.stage in ("all", "report"):
        stage_report()
    if a.stage == "status":
        k = load_known()
        print(json.dumps({"findings": len([x for x in k if not x.startswith("_")]),
                          "verdicts": [v["verdict"] for x, v in k.items() if not x.startswith("_")]}))
    print(f"done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
