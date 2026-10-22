"""Differential / metamorphic oracle using the Python duckdb module.

A query is run under several configurations that must be semantically
equivalent. Any mismatch in results (ignoring row order unless ORDER BY)
is a candidate optimizer / execution logic bug.

Includes metamorphic oracles:
- NoREC (Non-optimizing Reference Engine Construction): Q_opt == Q_ref
- TLP (Ternary Logic Partitioning): Q_whole == Q_parts (TRUE, FALSE, NULL)
- Schema introspection and deterministic predicate generators over seed tables.
"""
from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass

import duckdb

# Optimizer names differ between DuckDB versions (2.0.0 added 14 and removed 1), so they are
# read from the DuckDB source in use; the static v1.5.6 list is only a fallback.
_STATIC_OPTIMIZERS = [
    "expression_rewriter", "filter_pullup", "filter_pushdown", "empty_result_pullup",
    "cte_filter_pusher", "regex_range", "in_clause", "join_order", "deliminator",
    "unnest_rewriter", "unused_columns", "statistics_propagation", "common_subexpressions",
    "common_aggregate", "column_lifetime", "limit_pushdown", "row_group_pruner", "top_n",
    "top_n_window_elimination", "build_side_probe_side", "compressed_materialization",
    "duplicate_groups", "reorder_filter", "sampling_pushdown", "join_filter_pushdown",
    "materialized_cte", "sum_rewriter", "late_materialization", "cte_inlining",
    "common_subplan", "join_elimination", "window_self_join",
]


def _load_optimizers() -> list[str]:
    import os
    import re
    from pathlib import Path
    work = Path(os.environ.get("FUZZ_WORK", Path(__file__).resolve().parent.parent))
    f = work / "duckdb" / "src" / "common" / "enums" / "optimizer_type.cpp"
    try:
        names = re.findall(r'\{"([a-z_]+)",\s*OptimizerType::(?!INVALID|EXTENSION)', f.read_text())
        return names or list(_STATIC_OPTIMIZERS)
    except OSError:
        return list(_STATIC_OPTIMIZERS)


OPTIMIZERS = _load_optimizers()
# optimizers that did not exist in v1.5.6: brand-new, least-tested code
NEW_OPTIMIZERS = [o for o in OPTIMIZERS if o not in _STATIC_OPTIMIZERS]


def _disable(names: list[str]) -> list[str]:
    return [f"SET disabled_optimizers='{','.join(names)}'"]


CONFIGS: dict[str, list[str]] = {
    "default": [],
    "no_optimizers": _disable(OPTIMIZERS),
    "single_thread": ["SET threads=1"],
    "many_threads": ["SET threads=8"],
    "no_insertion_order": ["SET preserve_insertion_order=false"],
}
if NEW_OPTIMIZERS:      # compare against a run with only the brand-new optimizers switched off
    CONFIGS["no_new_optimizers"] = _disable(NEW_OPTIMIZERS)
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
        if math.isinf(v):
            return v
        return float(f"{v:.7g}")      # 7 significant digits: tolerate summation-order noise
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


# -----------------------------------------------------------------------------
# Metamorphic Oracles: Schema Introspection & Predicate Generation
# -----------------------------------------------------------------------------

def extract_schema(con: duckdb.DuckDBPyConnection) -> dict[str, list[dict[str, str]]]:
    """Extract base tables and views with their columns from the main schema.
    
    Returns:
        dict mapping table_name -> list of {"name": col_name, "type": data_type}
    """
    tables = con.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'main'
        ORDER BY table_name
    """).fetchall()

    schema: dict[str, list[dict[str, str]]] = {}
    for (tname,) in tables:
        if tname.startswith(("duckdb_", "sqlite_", "pragma_", "pg_")):
            continue
        cols = con.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_schema = 'main' AND table_name = ?
            ORDER BY ordinal_position
        """, [tname]).fetchall()
        schema[tname] = [{"name": cname, "type": dtype} for cname, dtype in cols]
    return schema


def classify_type(data_type: str) -> str:
    """Classify a DuckDB data type string into a canonical family."""
    t = data_type.upper().strip()
    if t.startswith(("STRUCT", "MAP", "UNION", "ARRAY")) or t.endswith("]"):
        return "other"
    if any(k in t for k in ("INT", "SHORT", "LONG", "BYTE")):
        return "integer"
    if any(k in t for k in ("FLOAT", "DOUBLE", "REAL", "DECIMAL", "NUMERIC")):
        return "float"
    if any(k in t for k in ("VARCHAR", "TEXT", "CHAR", "STRING", "BPCHAR")):
        return "string"
    if "BOOL" in t:
        return "boolean"
    if "DATE" in t:
        return "date"
    if "TIME" in t:
        return "timestamp"
    return "other"


def generate_predicates(columns: list[dict[str, str]], max_predicates: int = 10,
                        seed: int | None = 42) -> list[str]:
    """Deterministically generate type-safe boolean predicates over table columns.
    
    Generates atomic predicates (comparisons, null checks, IN, BETWEEN), inter-column
    comparisons, and compound predicates (AND, OR, NOT).
    """
    if not columns:
        return []

    rng = random.Random(seed)
    atomic: list[str] = []
    by_family: dict[str, list[str]] = {}

    for col_info in columns:
        c = col_info["name"].replace('"', '""')
        qcol = f'"{c}"'
        fam = classify_type(col_info["type"])
        by_family.setdefault(fam, []).append(qcol)

        # Universal predicates
        atomic.append(f"{qcol} IS NULL")
        atomic.append(f"{qcol} IS NOT NULL")

        if fam == "integer":
            atomic.append(f"{qcol} = 0")
            atomic.append(f"{qcol} != 0")
            atomic.append(f"{qcol} > 0")
            atomic.append(f"{qcol} < 2048")
            atomic.append(f"{qcol} BETWEEN 0 AND 2048")
            atomic.append(f"{qcol} IN (0, 1, 2048)")
        elif fam == "float":
            atomic.append(f"{qcol} > 0.0")
            atomic.append(f"{qcol} <= 100.0")
            atomic.append(f"{qcol} = 0.0")
            atomic.append(f"{qcol} != -1.0")
        elif fam == "string":
            atomic.append(f"{qcol} = ''")
            atomic.append(f"{qcol} != 'test'")
            atomic.append(f"{qcol} LIKE '%a%'")
            atomic.append(f"length({qcol}) > 0")
            atomic.append(f"{qcol} IN ('', 'a', 'test')")
        elif fam == "boolean":
            atomic.append(f"{qcol} IS TRUE")
            atomic.append(f"{qcol} IS FALSE")
            atomic.append(f"NOT {qcol}")
        elif fam == "date":
            atomic.append(f"{qcol} > DATE '2020-01-01'")
            atomic.append(f"{qcol} <= DATE '1970-01-01'")
        elif fam == "timestamp":
            atomic.append(f"{qcol} >= TIMESTAMP '2020-01-01 00:00:00'")

    # Inter-column comparisons for same-family columns
    for fam, col_list in by_family.items():
        if len(col_list) >= 2 and fam in ("integer", "float", "string"):
            for c1, c2 in itertools.combinations(col_list[:4], 2):
                atomic.append(f"{c1} = {c2}")
                if fam in ("integer", "float"):
                    atomic.append(f"{c1} < {c2}")

    # Deduplicate atomic while preserving order
    unique_atomic = list(dict.fromkeys(atomic))

    # Generate compound predicates (AND, OR, NOT)
    compound: list[str] = []
    if len(unique_atomic) >= 2:
        for i in range(min(4, len(unique_atomic) - 1)):
            p1, p2 = unique_atomic[i], unique_atomic[i + 1]
            compound.append(f"({p1} AND {p2})")
            compound.append(f"({p1} OR {p2})")
            compound.append(f"NOT ({p1})")
        if len(unique_atomic) >= 3:
            p1, p2, p3 = unique_atomic[0], unique_atomic[1], unique_atomic[2]
            compound.append(f"(({p1} AND {p2}) OR {p3})")

    all_preds = unique_atomic + compound
    if len(all_preds) > max_predicates:
        rng.shuffle(all_preds)
        return all_preds[:max_predicates]
    return all_preds


def _quote_ident(name: str) -> str:
    """Safely escape and double-quote a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


# -----------------------------------------------------------------------------
# NoREC: Non-optimizing Reference Engine Construction (OSDI 2020)
# -----------------------------------------------------------------------------

def generate_norec_pairs(table_name: str, predicate: str,
                         columns: list[dict[str, str]] | None = None) -> list[tuple[str, str, str]]:
    """Generate NoREC (opt_query, ref_query, desc) pairs for a table and predicate.
    
    The optimized query exercises WHERE filter pushdown, index scans, and row-group
    pruning. The reference query computes the unoptimized ground truth via CASE WHEN.
    """
    qt = _quote_ident(table_name)
    pairs = []

    # Canonical NoREC: COUNT(*)
    opt_cnt = f"SELECT count(*) FROM {qt} WHERE {predicate}"
    ref_cnt = f"SELECT coalesce(sum(CASE WHEN {predicate} THEN 1 ELSE 0 END), 0) FROM {qt}"
    pairs.append((opt_cnt, ref_cnt, "count(*)"))

    # Numeric column aggregates (SUM, MIN)
    if columns:
        for cinfo in columns:
            if classify_type(cinfo["type"]) in ("integer", "float"):
                qc = _quote_ident(cinfo["name"])
                opt_sum = f"SELECT coalesce(sum({qc}), 0) FROM {qt} WHERE {predicate}"
                ref_sum = f"SELECT coalesce(sum(CASE WHEN {predicate} THEN {qc} ELSE NULL END), 0) FROM {qt}"
                pairs.append((opt_sum, ref_sum, f"sum({qc})"))

                opt_min = f"SELECT min({qc}) FROM {qt} WHERE {predicate}"
                ref_min = f"SELECT min(CASE WHEN {predicate} THEN {qc} ELSE NULL END) FROM {qt}"
                pairs.append((opt_min, ref_min, f"min({qc})"))
                break

    return pairs


def check_norec(con: duckdb.DuckDBPyConnection | None, table_name: str, predicate: str,
                setup: list[str] | None = None, agg: str = "count(*)") -> DiffResult:
    """Execute NoREC oracle check over table_name and predicate.
    
    Can run on an existing DuckDB connection `con` or on a new connection with `setup`.
    """
    qt = _quote_ident(table_name)
    if agg == "count(*)":
        opt = f"SELECT count(*) FROM {qt} WHERE {predicate}"
        ref = f"SELECT coalesce(sum(CASE WHEN {predicate} THEN 1 ELSE 0 END), 0) FROM {qt}"
    else:
        opt = f"SELECT coalesce(sum({agg}), 0) FROM {qt} WHERE {predicate}"
        ref = f"SELECT coalesce(sum(CASE WHEN {predicate} THEN {agg} ELSE NULL END), 0) FROM {qt}"

    if con is not None:
        try:
            r_opt = [_norm(r) for r in con.execute(opt).fetchall()]
            out_opt = ("ok", r_opt)
        except Exception as e:
            out_opt = ("error", type(e).__name__, str(e)[:200])
        try:
            r_ref = [_norm(r) for r in con.execute(ref).fetchall()]
            out_ref = ("ok", r_ref)
        except Exception as e:
            out_ref = ("error", type(e).__name__, str(e)[:200])
    else:
        out_opt = _run(setup or [], [], opt, False)
        out_ref = _run(setup or [], [], ref, False)

    if out_opt[0] == "ok" and out_ref[0] == "ok":
        if out_opt[1] != out_ref[1]:
            return DiffResult(
                sql=f"{opt}  /* vs */  {ref}",
                mismatch=True,
                detail=f"NoREC mismatch: opt={out_opt[1]} vs ref={out_ref[1]}",
                outputs={"opt": out_opt, "ref": out_ref, "predicate": predicate}
            )
        return DiffResult(sql=opt, mismatch=False, outputs={"opt": out_opt, "ref": out_ref})
    elif out_opt[0] != out_ref[0]:
        return DiffResult(
            sql=f"{opt}  /* vs */  {ref}",
            mismatch=True,
            detail=f"NoREC error divergence: opt={out_opt} vs ref={out_ref}",
            outputs={"opt": out_opt, "ref": out_ref, "predicate": predicate}
        )
    return DiffResult(sql=opt, mismatch=False, outputs={"opt": out_opt, "ref": out_ref})


# -----------------------------------------------------------------------------
# TLP: Ternary Logic Partitioning (PLDI 2020)
# -----------------------------------------------------------------------------

def generate_tlp_pairs(table_query: str, predicate: str, mode: str = "count") -> tuple[str, str]:
    """Generate Ternary Logic Partitioning (whole, parts) query pair.
    
    Modes:
      "count": COUNT(*) of whole vs SUM of partition counts.
      "rows":  SELECT * of whole vs UNION ALL of partition rows.
    """
    if mode == "rows":
        whole = f"SELECT * FROM ({table_query})"
        parts = (f"SELECT * FROM ({table_query}) WHERE {predicate} UNION ALL "
                 f"SELECT * FROM ({table_query}) WHERE NOT ({predicate}) UNION ALL "
                 f"SELECT * FROM ({table_query}) WHERE ({predicate}) IS NULL")
    else:
        whole = f"SELECT count(*) FROM ({table_query})"
        parts = (f"SELECT coalesce(sum(c), 0) FROM ("
                 f"SELECT count(*) AS c FROM ({table_query}) WHERE {predicate} UNION ALL "
                 f"SELECT count(*) AS c FROM ({table_query}) WHERE NOT ({predicate}) UNION ALL "
                 f"SELECT count(*) AS c FROM ({table_query}) WHERE ({predicate}) IS NULL)")
    return whole, parts


def check_tlp(table_query: str, predicate: str, setup: list[str] | None = None,
              con: duckdb.DuckDBPyConnection | None = None, mode: str = "count") -> DiffResult:
    """Ternary Logic Partitioning: Q == Q[p] U Q[NOT p] U Q[p IS NULL].

    `table_query` is e.g. 'SELECT * FROM t' or 't'; `predicate` a boolean expression.
    Can run with existing connection `con` or execute `setup` on a fresh connection.
    """
    whole, parts = generate_tlp_pairs(table_query, predicate, mode=mode)
    ordered = False        # partitions come back concatenated (TRUE, FALSE, NULL), so compare rows as multisets

    if con is not None:
        try:
            r_whole = [_norm(r) for r in con.execute(whole).fetchall()]
            if not ordered:
                r_whole = sorted(r_whole, key=repr)
            out_whole = ("ok", r_whole)
        except Exception as e:
            out_whole = ("error", type(e).__name__, str(e)[:200])
        try:
            r_parts = [_norm(r) for r in con.execute(parts).fetchall()]
            if not ordered:
                r_parts = sorted(r_parts, key=repr)
            out_parts = ("ok", r_parts)
        except Exception as e:
            out_parts = ("error", type(e).__name__, str(e)[:200])
    else:
        out_whole = _run(setup or [], [], whole, ordered)
        out_parts = _run(setup or [], [], parts, ordered)

    if out_whole[0] == "ok" and out_parts[0] == "ok":
        if out_whole[1] != out_parts[1]:
            return DiffResult(
                sql=f"{whole}  /* vs */  {parts}",
                mismatch=True,
                detail=f"TLP mismatch {out_whole[1]} vs {out_parts[1]}",
                outputs={"whole": out_whole, "parts": out_parts, "predicate": predicate}
            )
        return DiffResult(sql=whole, mismatch=False, outputs={"whole": out_whole, "parts": out_parts})
    elif out_whole[0] != out_parts[0]:
        return DiffResult(
            sql=f"{whole}  /* vs */  {parts}",
            mismatch=True,
            detail=f"TLP error divergence {out_whole} vs {out_parts}",
            outputs={"whole": out_whole, "parts": out_parts, "predicate": predicate}
        )
    return DiffResult(sql=whole, mismatch=False, outputs={"whole": out_whole, "parts": out_parts})


def run_norec_on_schema(con: duckdb.DuckDBPyConnection,
                        max_predicates_per_table: int = 5) -> list[DiffResult]:
    """Inspect schema and run NoREC checks across all tables."""
    schema = extract_schema(con)
    mismatches = []
    for tname, cols in schema.items():
        preds = generate_predicates(cols, max_predicates=max_predicates_per_table)
        for pred in preds:
            res = check_norec(con, tname, pred)
            if res.mismatch:
                mismatches.append(res)
    return mismatches


def run_tlp_on_schema(con: duckdb.DuckDBPyConnection,
                      max_predicates_per_table: int = 5) -> list[DiffResult]:
    """Inspect schema and run TLP checks across all tables."""
    schema = extract_schema(con)
    mismatches = []
    for tname, cols in schema.items():
        preds = generate_predicates(cols, max_predicates=max_predicates_per_table)
        for pred in preds:
            res = check_tlp(f'"{tname}"', pred, con=con)
            if res.mismatch:
                mismatches.append(res)
    return mismatches
