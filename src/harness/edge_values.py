"""Edge values per DuckDB type, as ready-to-embed SQL literal expressions. Injected into every generator's sandbox as the
read-only name EDGES (a dict: type name -> tuple of SQL strings), because values at the limits of a type's domain found
most of the 2026-11-07 bugs (upstream 28531, 28532, 28534, the UBIGINT move_constants bug): optimizer rewrites compute
new constants (c + 1 day, trunc(c + 1 part), c2 - c1, a cast of a statistics bound) without checking they still fit.

    python harness/edge_values.py        # check that every literal parses and has the stated type on the target build
"""
from __future__ import annotations

import json
import subprocess
import sys

I = {"TINYINT": (-2**7, 2**7 - 1), "SMALLINT": (-2**15, 2**15 - 1), "INTEGER": (-2**31, 2**31 - 1), "BIGINT": (-2**63, 2**63 - 1),
     "HUGEINT": (-2**127, 2**127 - 1), "UTINYINT": (0, 2**8 - 1), "USMALLINT": (0, 2**16 - 1), "UINTEGER": (0, 2**32 - 1),
     "UBIGINT": (0, 2**64 - 1), "UHUGEINT": (0, 2**128 - 1)}


def _ints() -> dict[str, tuple[str, ...]]:
    out = {}
    for t, (lo, hi) in I.items():
        vals = [lo, lo + 1, hi - 1, hi, 0, 1, hi // 2, hi // 2 + 1]
        if lo < 0:
            vals += [-1, lo // 2]
        vals += [v for v in (2**31 - 1, 2**31, 2**32, 2**53 + 1, 2**63 - 1, 2**63, 2**64 - 1) if lo <= v <= hi]
        out[t] = tuple(dict.fromkeys("CAST(%d AS %s)" % (v, t) for v in vals))
    return out


def _ts(t: str, frac: int) -> tuple[str, ...]:
    def f(s: str) -> str:
        return "CAST('%s' AS %s)" % (s, t)
    us = ".775806"[: frac + 1] if frac else ""
    vals = ["infinity", "-infinity", "294247-01-10 04:00:54" + us, "294247-01-10 00:00:00", "294247-01-09 23:59:59",
            "290309-12-22 (BC) 00:00:00", "290309-12-31 (BC) 23:59:59", "0001-01-01 00:00:00", "0001-12-31 (BC) 23:59:59",
            "1970-01-01 00:00:00", "1969-12-31 23:59:59", "2000-02-29 12:00:00", "9999-12-31 23:59:59"]
    if t == "TIMESTAMP_NS":
        vals = ["infinity", "-infinity", "2262-04-11 23:47:16.854775", "2262-04-11 00:00:00", "1677-09-21 00:12:43.145225",
                "1677-09-22 00:00:00", "1970-01-01 00:00:00", "1969-12-31 23:59:59.999999", "2000-02-29 12:00:00"]
    if t == "TIMESTAMPTZ":
        vals = [v if ("inf" in v or "BC" in v) else v + "+00" for v in vals]
    return tuple(f(v) for v in vals)


EDGES: dict[str, tuple[str, ...]] = {
    **_ints(),
    "FLOAT": ("CAST('nan' AS FLOAT)", "CAST('inf' AS FLOAT)", "CAST('-inf' AS FLOAT)", "CAST(-0.0 AS FLOAT)", "CAST(0.0 AS FLOAT)",
              "CAST(3.4028235e38 AS FLOAT)", "CAST(-3.4028235e38 AS FLOAT)", "CAST(1e-45 AS FLOAT)", "CAST(1.17549435e-38 AS FLOAT)",
              "CAST(16777216 AS FLOAT)", "CAST(16777217 AS FLOAT)", "CAST(0.1 AS FLOAT)", "CAST(2147483647.5 AS FLOAT)"),
    "DOUBLE": ("CAST('nan' AS DOUBLE)", "CAST('inf' AS DOUBLE)", "CAST('-inf' AS DOUBLE)", "CAST(-0.0 AS DOUBLE)", "CAST(0.0 AS DOUBLE)",
               "CAST(1.7976931348623157e308 AS DOUBLE)", "CAST(-1.7976931348623157e308 AS DOUBLE)", "CAST(4.9e-324 AS DOUBLE)",
               "CAST(2.2250738585072014e-308 AS DOUBLE)", "CAST(9007199254740992 AS DOUBLE)", "CAST(9007199254740993 AS DOUBLE)",
               "CAST(9223372036854775807 AS DOUBLE)", "CAST(18446744073709551615 AS DOUBLE)", "CAST(0.1 AS DOUBLE)", "CAST(0.5 AS DOUBLE)"),
    "DECIMAL(4,1)": ("CAST(999.9 AS DECIMAL(4,1))", "CAST(-999.9 AS DECIMAL(4,1))", "CAST(0.1 AS DECIMAL(4,1))", "CAST(-0.1 AS DECIMAL(4,1))", "CAST(0 AS DECIMAL(4,1))"),
    "DECIMAL(9,2)": ("CAST(9999999.99 AS DECIMAL(9,2))", "CAST(-9999999.99 AS DECIMAL(9,2))", "CAST(0.01 AS DECIMAL(9,2))", "CAST(0.05 AS DECIMAL(9,2))"),
    "DECIMAL(18,3)": ("CAST(999999999999999.999 AS DECIMAL(18,3))", "CAST(-999999999999999.999 AS DECIMAL(18,3))", "CAST(0.001 AS DECIMAL(18,3))", "CAST(0.0005 AS DECIMAL(18,4))"),
    "DECIMAL(38,0)": ("CAST(99999999999999999999999999999999999999 AS DECIMAL(38,0))", "CAST(-99999999999999999999999999999999999999 AS DECIMAL(38,0))", "CAST(0 AS DECIMAL(38,0))"),
    "DECIMAL(38,37)": ("CAST(9.9999999999999999999999999999999999999 AS DECIMAL(38,37))", "CAST(-9.9999999999999999999999999999999999999 AS DECIMAL(38,37))",
                       "CAST(0.0000000000000000000000000000000000001 AS DECIMAL(38,37))"),
    "DATE": tuple("CAST('%s' AS DATE)" % v for v in ("infinity", "-infinity", "5881580-07-10", "5881580-07-09", "5877642-06-25 (BC)",
                                                      "5877642-06-26 (BC)", "294247-01-10", "294247-01-11", "290309-12-22 (BC)",
                                                      "290309-12-21 (BC)", "0001-01-01", "0001-12-31 (BC)", "1970-01-01", "2000-02-29",
                                                      "1582-10-15", "9999-12-31")),
    "TIMESTAMP": _ts("TIMESTAMP", 6), "TIMESTAMP_S": _ts("TIMESTAMP_S", 0), "TIMESTAMP_MS": _ts("TIMESTAMP_MS", 3),
    "TIMESTAMP_NS": _ts("TIMESTAMP_NS", 6), "TIMESTAMPTZ": _ts("TIMESTAMPTZ", 6),
    "TIME": tuple("CAST('%s' AS TIME)" % v for v in ("00:00:00", "24:00:00", "23:59:59.999999", "12:00:00", "00:00:00.000001")),
    "TIMETZ": tuple("CAST('%s' AS TIMETZ)" % v for v in ("00:00:00+15:59", "24:00:00-15:59", "12:00:00+00", "23:59:59.999999-15:59", "00:00:00-15:59")),
    "INTERVAL": tuple("CAST('%s' AS INTERVAL)" % v for v in ("0 seconds", "1 month", "30 days", "-1 month", "2147483647 months",
                                                              "-2147483648 months", "2147483647 days", "9223372036854775807 microseconds",
                                                              "1 month -30 days", "24 hours")),
    "VARCHAR": ("''", "'a'", "'A'", "' '", "'aaaaaaaa'", "'aaaaaaaaa'", "'aaaaaaaaZ'", "'ÿÿÿÿÿÿÿÿ'",
                "'é'", "'é'", "'\U0001F600'", "'İ'", "'%'", "'_'", "'\\'", "'x' || repeat('y', 300)", "'NULL'", "'nan'"),
    "BLOB": ("''::BLOB", "'\\x00'::BLOB", "'\\xFF\\xFF\\xFF\\xFF\\xFF\\xFF\\xFF\\xFF\\xFF'::BLOB", "'abc'::BLOB"),
    "UUID": ("CAST('00000000-0000-0000-0000-000000000000' AS UUID)", "CAST('ffffffff-ffff-ffff-ffff-ffffffffffff' AS UUID)",
             "CAST('7fffffff-ffff-ffff-ffff-ffffffffffff' AS UUID)", "CAST('80000000-0000-0000-0000-000000000000' AS UUID)"),
    "BOOLEAN": ("true", "false", "CAST(NULL AS BOOLEAN)"),
}
EDGES = {k: tuple(v) for k, v in EDGES.items()}


def check() -> int:
    """Every literal must evaluate on the target build and have the stated type (else generators would waste cases)."""
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    import devcheck
    code = ("import duckdb,json,sys;E=json.load(sys.stdin);c=duckdb.connect();bad=[]\n"
            "for t,vs in E.items():\n"
            "  for v in vs:\n"
            "    try:\n"
            "      ty=c.execute('SELECT typeof('+v+')').fetchone()[0]\n"
            "      if not (ty==t or (t.startswith('DECIMAL') and ty.startswith('DECIMAL')) or t in ('VARCHAR','BLOB','BOOLEAN') or (t=='TIMESTAMPTZ' and ty=='TIMESTAMP WITH TIME ZONE') or (t=='TIMETZ' and ty=='TIME WITH TIME ZONE')): bad.append((t,v,ty))\n"
            "    except Exception as e: bad.append((t,v,str(e)[:80]))\n"
            "print(json.dumps(bad))")
    p = subprocess.run([str(devcheck.DEV_PY), "-I", "-c", code], input=json.dumps(EDGES), capture_output=True, text=True, timeout=300)
    bad = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else [("?", "?", p.stderr[-200:])]
    n = sum(len(v) for v in EDGES.values())
    for b in bad:
        print("BAD", b)
    print(f"{n - len(bad)}/{n} edge literals ok across {len(EDGES)} types")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(check())
