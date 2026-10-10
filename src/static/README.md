# static/ — program analysis and hints  (owner: Jahnvi)

Tasks (see docs/WORKPLAN.md): **J1** choose CodeQL vs fallback, **J2** extract
hints (core), **J3** join with coverage gaps, **J7** evaluate hints.

Output contract: `static/hints.json`, a JSON list; each object must validate
against `hints.schema.json`. The LLM stage (llm/generate.py) reads this file.

Files
- `extract_hints.py` : entry point (stub). `python static/extract_hints.py --src $FUZZ_WORK/duckdb --out static/hints.json`
- `hints.schema.json`: the agreed schema (change only after telling the others)
- `queries/`         : put CodeQL `.ql` files here if you go the CodeQL route

Directories to analyse first: src/optimizer, src/planner, src/execution,
src/function, src/storage. Record limits and false positives in this README
(they feed the paper's Threats to Validity).
