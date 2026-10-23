"""Feature cards: valid DuckDB 2.0 SQL examples for the new engine code, taken from the tests added since 1.5.x.

    python llm/feature_cards.py build [--seeds corpus/seeds-v200-added] [--out results/feature-cards.json]
    python llm/feature_cards.py show  <source file path>          # which examples a hint in that file would get

An LLM trained before 2.0 does not know the new syntax (CREATE TRIGGER ... REFERENCING, NEAREST joins, variant_*
functions, aggregate-state export, ...), so unguided it invents statements that fail EXPLAIN and wastes the prompt. The
examples here are statements from DuckDB's own *added* test files, so they are valid by construction. generate.py
appends the matching examples to a target ("this C++ code has never run: reach it") so the model imitates real syntax.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "harness"))
import runner  # noqa: E402

WORK = runner.WORK

# (card name, regex over the hint's source path, regex over added-test seed names, one-line description)
CARDS = [
    ("triggers", r"trigger|bind_(insert|update|delete|merge)|physical_(insert|update|delete)|dml", r"^trigger__",
     "CREATE TRIGGER <name> {BEFORE|AFTER} {INSERT|UPDATE [OF col]|DELETE} ON <table> [REFERENCING OLD TABLE AS o NEW TABLE AS n] "
     "FOR EACH {STATEMENT|ROW} <one DML statement>; row triggers use NEW./OLD. column references"),
    ("correlated subqueries and dependent joins (delim join rewrite)", r"delim_join|flatten_dependent|subquery|dependent", r"^subquery__|lateral|correlated",
     "correlated scalar / EXISTS / IN subqueries, LATERAL joins; 2.0 rewrites dependent joins into materialized CTEs (setting delim_join_as_cte)"),
    ("new optimizers", r"src/optimizer/", r"^optimizer__",
     "2.0 adds optimizers such as partial_aggregate_pushdown, projection_pullup, outer_join_simplification, window_rewriter, "
     "distinct_aggregate_rewrite, aggregate_reuse, type_pushdown, grouping_sets; queries must keep their results with them on or off"),
    ("window functions", r"window", r"^window__", "window functions, frames (ROWS/RANGE/GROUPS, EXCLUDE), IGNORE NULLS, fill()"),
    ("aggregates, ordered aggregates and state export", r"aggregate|quantile|extension/core_functions", r"^aggregate__|^function__",
     "aggregates with ORDER BY/FILTER/DISTINCT, quantile/list-based aggregates, aggregate state export (to_aggregate_state, combine_aggr)"),
    ("json and variant", r"extension/json|variant", r"^json__|variant", "json_* functions (json_set, json_insert, json_deep_merge, ...) and the VARIANT type / variant_* functions"),
    ("types and casts", r"src/common/types|cast|vector", r"^types__|^cast__", "type system, casts, nested types, the new TUPLE and TIMESTAMPTZ_NS types"),
    ("joins", r"join|physical_(hash|nested|piecewise|asof|blockwise|cross)", r"^join__", "hash / nested-loop / ASOF / POSITIONAL / ANTI / SEMI / NEAREST joins, join ordering"),
    ("CTEs", r"cte|recursive", r"^cte__", "WITH / WITH RECURSIVE / MATERIALIZED CTEs and data-modifying CTEs"),
    ("DML: insert, update, delete, merge", r"merge|insert|update|delete|constraint|index", r"^merge__|^update__|^insert__|^delete__|^constraints__|^index__",
     "INSERT ... ON CONFLICT, UPDATE ... FROM, DELETE ... USING, MERGE INTO with RETURNING, DEFAULT values"),
    ("parser", r"src/parser", r"^parser__|^peg_parser__", "SQL syntax forms accepted by the (PEG) parser"),
    ("storage and transactions", r"src/storage|transaction", r"^transactions__|^alter__|^catalog__", "ALTER TABLE, catalog changes, transactions, rollback"),
    ("table functions", r"table_function|src/function/table", r"^table_function__", "table functions and scans"),
]

SKIP = re.compile(r"^\s*use\b|\{TEMP_DIR\}|\{DATA_DIR\}|__TEST_DIR__|read_\w+\(|copy\b|attach\b|install\b|load\b|export\b|import\b|http|\bfile\b", re.I)


def mini_script(path: Path, budget: int = 650) -> str | None:
    """First consecutive statements of a test file (setup + queries) that fit in `budget` characters."""
    out, size = [], 0
    for st in runner.split_statements(path.read_text(errors="replace")):
        st = st.strip()
        if not st or SKIP.search(st):
            if out:
                break
            continue
        st = st if st.endswith(";") else st + ";"
        if size + len(st) > budget:
            break
        out.append(st)
        size += len(st)
    has_query = any(re.match(r"(?i)(select|with|insert|update|delete|merge|from)", x) for x in out)
    return "\n".join(out) if len(out) >= 2 and has_query else None


def build_cards(seeds: Path, per_card: int, rng: random.Random) -> list[dict]:
    seed_files = sorted(seeds.glob("*.sql"))
    cards = []
    for name, hint_re, seed_re, desc in CARDS:
        pool = [f for f in seed_files if re.search(seed_re, f.name)]
        rng.shuffle(pool)
        ex: list[str] = []
        for f in pool:
            sc = mini_script(f)
            if sc and all(sc != e["sql"] for e in ex):
                ex.append({"seed": f.stem, "sql": sc})
            if len(ex) >= per_card * 12:
                break
        cards.append({"name": name, "hint_re": hint_re, "description": desc, "examples": ex, "seed_files": len(pool)})
    return cards


def load_cards(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text())


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 2 and t not in ("src", "cpp", "the", "test", "optimizer", "planner", "execution")}


def examples_for(cards: list[dict], hint_file: str, k: int, rng: random.Random) -> tuple[str, str]:
    """Return (card name, prompt text) for the first card whose pattern matches the hint's source file; ('', '') if none.

    Examples from test files whose name shares words with the hint's file name (e.g. multi_stage_aggregate_rewriter.cpp ->
    optimizer__multi_stage_aggregate.sql) are preferred; the rest are random."""
    for c in cards:
        if c["examples"] and re.search(c["hint_re"], hint_file, re.I):
            want = _tokens(Path(hint_file).stem)
            exs = [e if isinstance(e, dict) else {"seed": "", "sql": e} for e in c["examples"]]
            scored = sorted(exs, key=lambda e: (-len(want & _tokens(e["seed"])), rng.random()))
            body = "\n\n".join(e["sql"] for e in scored[:k])
            return c["name"], (f"\nThis code belongs to a feature that is new or changed in DuckDB 2.0: {c['name']} - {c['description']}.\n"
                               f"Valid DuckDB 2.0 scripts for this feature (copy their syntax exactly, then vary the details to reach the code above):\n\n```sql\n{body}\n```\n")
    return "", ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["build", "show"])
    ap.add_argument("file", nargs="?", default="")
    ap.add_argument("--seeds", default=str(WORK / "corpus" / "seeds-v200-added"))
    ap.add_argument("--out", default=str(WORK / "results" / "feature-cards.json"))
    ap.add_argument("--per-card", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    if a.cmd == "build":
        cards = build_cards(Path(a.seeds), a.per_card, rng)
        Path(a.out).write_text(json.dumps(cards, indent=1))
        for c in cards:
            print(f"{c['name'][:52]:52} seeds={c['seed_files']:4} examples={len(c['examples'])}")
        print("wrote", a.out)
    else:
        cards = load_cards(a.out)
        name, text = examples_for(cards, a.file, 2, rng)
        print(name or "(no card matches)"); print(text)


if __name__ == "__main__":
    main()
