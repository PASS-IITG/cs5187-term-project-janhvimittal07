"""Generate SQL for uncovered regions.  Owner: Prabhnoor.  Tasks: P2, P3.

    python llm/generate.py --run r1 --n 50

TODO (P2): build prompt from (schema, uncovered region, hint, examples);
  parse a JSON/SQL batch; validate each statement with EXPLAIN on a duckdb
  connection that has the schema loaded; one repair retry with the error text;
  write survivors to $FUZZ_WORK/corpus/llm/<run>/NNN.sql
TODO (P3): loop: run on build-cov via coverage.cov.Coverage.run_and_merge,
  recompute uncovered_regions(), re-prompt.
"""
from __future__ import annotations


def build_prompt(schema_sql: str, region_snippet: str, hint: dict | None = None) -> str:
    raise NotImplementedError("P2")


def generate_batch(prompt: str) -> list[str]:
    raise NotImplementedError("P2")


def validate(sql: str, schema_sql: str) -> tuple[bool, str]:
    """(ok, error_text) using EXPLAIN on a fresh connection."""
    raise NotImplementedError("P2")


def main() -> None:
    raise NotImplementedError("P2/P3")


if __name__ == "__main__":
    main()
