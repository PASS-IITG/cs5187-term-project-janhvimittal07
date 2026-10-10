"""Differential / metamorphic oracle using the Python duckdb module.

A query is run under several configurations that must be semantically
equivalent. Any mismatch in results (ignoring row order unless ORDER BY)
is a candidate optimizer / execution logic bug.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import duckdb

# Names taken from duckdb/src/common/enums/optimizer_type.cpp (v1.5.6).
OPTIMIZERS = [
    "expression_rewriter", "filter_pullup", "filter_pushdown", "empty_result_pullup",
    "cte_filter_pusher", "regex_range", "in_clause", "join_order", "deliminator",
    "unnest_rewriter", "unused_columns", "statistics_propagation", "common_subexpressions",
    "common_aggregate", "column_lifetime", "limit_pushdown", "row_group_pruner", "top_n",
    "top_n_window_elimination", "build_side_probe_side", "compressed_materialization",
    "duplicate_groups", "reorder_filter", "sampling_pushdown", "join_filter_pushdown",
    "materialized_cte", "sum_rewriter", "late_materialization", "cte_inlining",
    "common_subplan", "join_elimination", "window_self_join",
]


def _disable(names):
    return [f"SET disabled_optimizers='{','.join(names)}'"]


CONFIGS: dict[str, list[str]] = {
    "default": [],
    "no_optimizers": _disable(OPTIMIZERS),
    "single_thread": ["SET threads=1"],
    "many_threads": ["SET threads=8"],
    "no_insertion_order": ["SET preserve_insertion_order=false"],
}
# one config per optimizer: isolates which pass causes a mismatch
PER_OPTIMIZER = {f"no_{o}": _disable([o]) for o in OPTIMIZERS}


@dataclass
class DiffResult:
    sql: str
    mismatch: bool
    detail: str = ""
    outputs: dict | None = None


def _norm(v):
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        return round(v, 6)
    if isinstance(v, (list, tuple)):
        return tuple(_norm(x) for x in v)
    if isinstance(v, dict):
        return tuple(sorted((k, _norm(x)) for k, x in v.items()))
    return v


def _run(setup: list[str], pre: list[str], query: str, ordered: bool):
    con = duckdb.connect(":memory:")
    try:
        for s in pre + setup:
            con.execute(s)
        rows = con.execute(query).fetchall()
        rows = [_norm(r) for r in rows]
        return ("ok", rows if ordered else sorted(rows, key=repr))
    except duckdb.Error as e:
        return ("error", type(e).__name__)      # error *class* only; text may differ
    finally:
        con.close()


def check(query: str, setup: list[str] | None = None, configs=None) -> DiffResult:
    setup = setup or []
    ordered = "order by" in query.lower()
    outs = {}
    for name in (configs or CONFIGS):
        outs[name] = _run(setup, {**CONFIGS, **PER_OPTIMIZER}[name], query, ordered)
    base = outs["default"]
    for name, o in outs.items():
        if o != base:
            return DiffResult(query, True, f"{name} != default", outs)
    return DiffResult(query, False, outputs=outs)


def bisect_optimizer(query: str, setup: list[str] | None = None) -> list[str]:
    """After a mismatch, name the optimizer(s) whose removal changes the result."""
    base = _run(setup or [], [], query, "order by" in query.lower())
    return [o for o in OPTIMIZERS
            if _run(setup or [], PER_OPTIMIZER[f"no_{o}"], query, "order by" in query.lower()) != base]


def check_tlp(table_query: str, predicate: str, setup=None) -> DiffResult:
    """Ternary Logic Partitioning: Q == Q[p] U Q[NOT p] U Q[p IS NULL].

    `table_query` is e.g. 'SELECT * FROM t'; `predicate` a boolean expression.
    """
    setup = setup or []
    whole = f"SELECT count(*) FROM ({table_query})"
    parts = (f"SELECT sum(c) FROM ("
             f"SELECT count(*) c FROM ({table_query}) WHERE {predicate} UNION ALL "
             f"SELECT count(*) FROM ({table_query}) WHERE NOT ({predicate}) UNION ALL "
             f"SELECT count(*) FROM ({table_query}) WHERE ({predicate}) IS NULL)")
    a = _run(setup, [], whole, False)
    b = _run(setup, [], parts, False)
    if a[0] == "ok" and b[0] == "ok" and a[1] != b[1]:
        return DiffResult(f"{whole} vs {parts}", True, f"TLP mismatch {a[1]} vs {b[1]}", {"whole": a, "parts": b})
    return DiffResult(table_query, False)
