"""LLM query generator: uncovered code + schema -> validated DuckDB SQL (Task P2).

    python llm/generate.py --run r1 --n-prompts 20 --per-prompt 10

Per prompt: pick a target (an uncovered region from coverage data, else a
static hint, else none), show the model a schema and the C++ snippet, ask for
SQL, validate every statement with EXPLAIN on a fresh in-memory DuckDB that
has the schema loaded, send failures back once for repair, and write each
surviving batch as a self-contained script to $FUZZ_WORK/corpus/llm/<run>/.
Metrics (one JSON row per prompt) go to $FUZZ_WORK/results/llm-<run>.jsonl.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC / "harness"))
sys.path.insert(0, str(SRC / "coverage"))
import runner  # noqa: E402
from client import LLM, LLMError  # noqa: E402

WORK = runner.WORK
FORBIDDEN = re.compile(r"^\s*(attach|detach|install|load|copy\b.*\bto\b|export|import|pragma\s+database|"
                       r"set\s+(file_search_path|home_directory|temp_directory|extension)|\.)", re.I)

SYSTEM = ("You are a fuzzing assistant for the DuckDB database engine. You write valid DuckDB SQL "
          "that exercises unusual code paths. Output only SQL.")

PROMPT = """The following tables already exist in the database. Use these tables and columns exactly.

```sql
{schema}
```
{target}
Write {n} different DuckDB SQL queries (SELECT, with nesting such as subqueries, CTEs, window functions, joins,
nested STRUCT/LIST/MAP access, aggregates with FILTER, boundary values like 2047/2048/2049, NULLs) that would
make DuckDB execute that code{ctx_word}. Each query must be a single statement ending with ';'.
Output exactly one ```sql block and nothing else."""

REPAIR = """Some of the queries you wrote failed on DuckDB. The schema is:

```sql
{schema}
```
Fix each failing query so it is valid DuckDB SQL against this schema and keeps the same intent.
Output exactly one ```sql block with the corrected statements, one per line, each ending with ';'.

Failing queries and errors:
{failures}"""


# ---------------------------------------------------------------- targets
def load_schemas(path: Path) -> dict[str, str]:
    return {f.stem: f.read_text() for f in sorted(path.glob("*.sql"))}


def _fmt_target(file: str, start: int, end: int, snippet: str) -> str:
    return (f"\nThis C++ code in DuckDB ({file}, lines {start}-{end}) has never been executed. "
            f"Your queries should reach it:\n\n```cpp\n{snippet[:2500]}\n```\n")


def pick_targets(n: int, source: str, rng: random.Random, filters: list[str] | None = None) -> list[dict]:
    """Return n target dicts {text, file, start, end}. text may be '' (no target)."""
    out: list[dict] = []
    if source in ("auto", "cov"):
        try:
            from cov import Coverage
            c = Coverage(WORK / "results" / "cov")
            if c.profdata.exists():
                regs = c.uncovered_regions(filters, top=max(50, n * 3), by="hints")
                regs = [r for r in regs if r.snippet]
                rng.shuffle(regs)
                out = [{"text": _fmt_target(r.file, r.start, r.end, r.snippet), "file": r.file,
                        "start": r.start, "end": r.end, "source": "coverage"} for r in regs[:n]]
        except Exception as e:                    # noqa: BLE001
            print(f"[warn] coverage targets unavailable: {e!r}", file=sys.stderr)
    if len(out) < n and source in ("auto", "hints"):
        hp = SRC / "static" / "hints.json"
        if hp.exists():
            hs = [h for h in json.loads(hp.read_text()) if h.get("kind") in ("sink", "branch") and h.get("context")]
            rng.shuffle(hs)
            for h in hs[: n - len(out)]:
                out.append({"text": _fmt_target(h["file"], h["line"], h["line"], h["context"]),
                            "file": h["file"], "start": h["line"], "end": h["line"], "source": "hint"})
    while len(out) < n:
        out.append({"text": "", "file": "", "start": 0, "end": 0, "source": "none"})
    return out


# ---------------------------------------------------------------- parse / validate
def parse_sql(text: str) -> list[str]:
    blocks = re.findall(r"```(?:sql|SQL|duckdb)?\s*\n(.*?)```", text, re.S)
    body = "\n".join(blocks) if blocks else text
    stmts = []
    for s in runner.split_statements(body):
        s = s.strip()
        s = re.sub(r"^--.*$", "", s, flags=re.M).strip()       # drop comment lines
        if s and s != ";" and not FORBIDDEN.match(s):
            stmts.append(s if s.endswith(";") else s + ";")
    return list(dict.fromkeys(stmts))


def validate(schema_sql: str, stmts: list[str]) -> list[tuple[str, bool, str]]:
    """EXPLAIN each statement on a fresh connection with the schema loaded."""
    import duckdb
    con = duckdb.connect(":memory:")
    try:
        for s in runner.split_statements(schema_sql):
            con.execute(s)
        res = []
        for q in stmts:
            try:
                con.execute("EXPLAIN " + q.rstrip(";"))
                res.append((q, True, ""))
            except Exception as e:                # noqa: BLE001
                res.append((q, False, str(e).splitlines()[0][:300]))
        return res
    finally:
        con.close()


def generate_batch(llm: LLM, schema_sql: str, target: dict, n: int, temperature: float) -> dict:
    prompt = PROMPT.format(schema=schema_sql.strip()[:6000], target=target["text"], n=n,
                           ctx_word=" shown above" if target["text"] else "")
    raw = llm.complete(prompt, system=SYSTEM, temperature=temperature)
    stmts = parse_sql(raw)
    first = validate(schema_sql, stmts)
    good = [q for q, ok, _ in first if ok]
    bad = [(q, e) for q, ok, e in first if not ok]
    repaired = 0
    if bad:
        fails = "\n".join(f"{q}\n-- error: {e}" for q, e in bad[:10])
        try:
            fixed = parse_sql(llm.complete(REPAIR.format(schema=schema_sql.strip()[:6000], failures=fails),
                                           system=SYSTEM, temperature=0.2))
            for q, ok, _ in validate(schema_sql, fixed):
                if ok and q not in good:
                    good.append(q)
                    repaired += 1
        except LLMError as e:
            print(f"[warn] repair call failed: {e}", file=sys.stderr)
    return {"queries": good, "n_generated": len(stmts), "n_valid_first": len(stmts) - len(bad),
            "n_repaired": repaired, "n_valid": len(good)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--n-prompts", type=int, default=10)
    ap.add_argument("--per-prompt", type=int, default=10)
    ap.add_argument("--schemas", default=str(WORK / "gen_out"))
    ap.add_argument("--targets", default="auto", choices=["auto", "cov", "hints", "none"])
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    schemas = load_schemas(Path(a.schemas))
    if not schemas:
        sys.exit(f"no .sql schemas in {a.schemas}; run: python gen/generate_schema.py --out-dir {a.schemas}")
    rng = random.Random(a.seed)
    llm = LLM()
    out_dir = WORK / "corpus" / "llm" / a.run
    out_dir.mkdir(parents=True, exist_ok=True)
    log = WORK / "results" / f"llm-{a.run}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    targets = [{"text": "", "file": "", "start": 0, "end": 0, "source": "none"}] * a.n_prompts \
        if a.targets == "none" else pick_targets(a.n_prompts, a.targets, rng)
    names = sorted(schemas)
    t0 = time.time()
    tot = {"generated": 0, "valid": 0}
    with open(log, "a") as fh:
        for i, tg in enumerate(targets):
            name = rng.choice(names)
            try:
                r = generate_batch(llm, schemas[name], tg, a.per_prompt, a.temperature)
            except LLMError as e:
                print(f"[{i}] LLM error: {e}", file=sys.stderr)
                continue
            if r["queries"]:
                (out_dir / f"{i:04d}__{name}.sql").write_text(schemas[name].rstrip() + "\n\n" +
                                                              "\n".join(r["queries"]) + "\n")
            tot["generated"] += r["n_generated"]
            tot["valid"] += r["n_valid"]
            fh.write(json.dumps({"i": i, "t_sec": round(time.time() - t0, 1), "schema": name,
                                 "target_source": tg["source"], "target_file": tg["file"],
                                 "target_lines": [tg["start"], tg["end"]], **{k: v for k, v in r.items()
                                                                              if k != "queries"}}) + "\n")
            fh.flush()
            print(f"[{i}] {name:22s} gen={r['n_generated']:2d} valid_first={r['n_valid_first']:2d} "
                  f"repaired={r['n_repaired']} kept={r['n_valid']:2d}  ({tg['source']})", flush=True)
    rate = tot["valid"] / tot["generated"] if tot["generated"] else 0.0
    print(f"done: {tot['valid']}/{tot['generated']} valid ({rate:.0%}) -> {out_dir}")


if __name__ == "__main__":
    main()
