"""llvm-cov ingestion: cumulative merge, lcov export, uncovered-region report,
per-function coverage, and static hint cross-referencing (Task J3).

Usage (library):
    c = Coverage("results/cov")                 # work dir
    c.run_and_merge(sql_text)                   # run on build-cov, fold into cumulative profile
    print(c.summary(["src/optimizer", "src/execution"]))
    print(c.hints_coverage())                   # static hint hit-rate analysis
    for r in c.uncovered_regions(["src/optimizer"], top=10, by="hints"):
        print(r.file, r.start, r.end, r.hints_count)

CLI:
    python src/coverage/cov.py report [dir-prefix ...]
    python src/coverage/cov.py uncovered [--by=size|hints|sinks] [--top=N] [dir-prefix ...]
    python src/coverage/cov.py hints [--kind=sink|branch|magic_const] [--top=N] [dir-prefix ...]
    python src/coverage/cov.py functions [--uncovered] [--top=N] [dir-prefix ...]
    python src/coverage/cov.py export-report [--out=PATH] [dir-prefix ...]
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("FUZZ_WORK", Path(__file__).resolve().parent.parent))  # duckdb/, build-*/, corpus/, results/
sys.path.insert(0, str(CODE / "harness"))
import runner  # noqa: E402

BIN = WORK / "build-cov" / "duckdb"
SRC = WORK / "duckdb"
DEFAULT_HINTS = CODE / "static" / "hints.json"
DEFAULT_FILTERS = [
    "src/optimizer", "src/planner", "src/execution", "src/function",
    "src/parser", "src/storage", "src/common",
]


def demangle_batch(names: list[str]) -> dict[str, str]:
    """Fast batch demangling of C++ symbols via c++filt."""
    if not names:
        return {}
    try:
        proc = subprocess.run(
            ["c++filt"],
            input="\n".join(names),
            capture_output=True,
            text=True,
            check=True,
        )
        demangled = proc.stdout.splitlines()
        return dict(zip(names, demangled))
    except Exception:
        return {n: n for n in names}


@dataclass
class Region:
    file: str
    start: int
    end: int
    snippet: str
    hints: list[dict] = field(default_factory=list)

    @property
    def size(self) -> int:
        return self.end - self.start + 1

    @property
    def hints_count(self) -> int:
        return len(self.hints)

    @property
    def sinks_count(self) -> int:
        return sum(1 for h in self.hints if h.get("kind") == "sink")


class Coverage:
    def __init__(self, workdir: str | Path = WORK / "results" / "cov"):
        self.dir = Path(workdir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.profdata = self.dir / "all.profdata"
        # file_path -> (lines_dict, branches_dict, functions_dict)
        self._lcov: dict[str, tuple[dict[int, int], dict[tuple, int], dict[str, dict]]] | None = None
        self._cached_hints: list[dict] | None = None
        self._cached_hints_by_file: dict[str, list[dict]] | None = None

    # ---- collection ----
    def run_and_merge(self, sql: str, timeout: float = 60.0) -> runner.Result:
        with tempfile.TemporaryDirectory(dir=self.dir) as td:
            raw = Path(td) / "%p.profraw"
            r = runner.run_sql(sql, "cov", timeout, cwd=SRC,
                               env_extra={"LLVM_PROFILE_FILE": str(raw)})
            self.merge_raw(list(Path(td).glob("*.profraw")))
        return r

    def merge_raw(self, raws: list[Path]) -> None:
        if not raws:
            return
        cmd = ["llvm-profdata", "merge", "-sparse", "-o", str(self.profdata) + ".new"]
        if self.profdata.exists():
            cmd.append(str(self.profdata))
        cmd += [str(p) for p in raws]
        subprocess.run(cmd, check=True, capture_output=True)
        os.replace(str(self.profdata) + ".new", self.profdata)
        self._lcov = None

    # ---- export / parse ----
    def lcov(self) -> dict[str, tuple[dict[int, int], dict[tuple, int], dict[str, dict]]]:
        """Export and parse LCOV data into lines, branches, and functions."""
        if self._lcov is not None:
            return self._lcov
        if not self.profdata.exists():
            return {}
        out = subprocess.run(
            ["llvm-cov", "export", str(BIN), f"-instr-profile={self.profdata}",
             "-format=lcov", "-ignore-filename-regex=third_party|/usr/"],
            check=True, capture_output=True, text=True).stdout

        files: dict[str, tuple[dict[int, int], dict[tuple, int], dict[str, dict]]] = {}
        cur = None
        for ln in out.splitlines():
            if ln.startswith("SF:"):
                p = ln[3:]
                try:
                    p = str(Path(p).resolve().relative_to(SRC))
                except ValueError:
                    pass
                cur = files.setdefault(p, ({}, {}, {}))
            elif cur is not None:
                if ln.startswith("DA:"):
                    n, c = ln[3:].split(",")[:2]
                    cur[0][int(n)] = max(cur[0].get(int(n), 0), int(c))
                elif ln.startswith("BRDA:"):
                    n, blk, br, c = ln[5:].split(",")
                    k = (int(n), blk, br)
                    cur[1][k] = max(cur[1].get(k, 0), 0 if c == "-" else int(c))
                elif ln.startswith("FN:"):
                    parts = ln[3:].split(",", 1)
                    if len(parts) == 2:
                        fn_line, fn_name = int(parts[0]), parts[1]
                        cur[2].setdefault(fn_name, {"line": fn_line, "count": 0})
                elif ln.startswith("FNDA:"):
                    parts = ln[5:].split(",", 1)
                    if len(parts) == 2:
                        fn_count, fn_name = int(parts[0]), parts[1]
                        if fn_name in cur[2]:
                            cur[2][fn_name]["count"] = max(cur[2][fn_name]["count"], fn_count)
                        else:
                            cur[2][fn_name] = {"line": 0, "count": fn_count}

        self._lcov = files
        return files

    def _sel(self, filters=None):
        filters = filters or DEFAULT_FILTERS
        return {f: v for f, v in self.lcov().items() if any(f.startswith(p) for p in filters)}

    # ---- static hints ingestion ----
    def load_hints(self, hints_path: str | Path | None = None) -> list[dict]:
        if self._cached_hints is not None and hints_path is None:
            return self._cached_hints
        hp = Path(hints_path or DEFAULT_HINTS)
        if not hp.exists():
            return []
        try:
            hints = json.loads(hp.read_text(errors="replace"))
        except Exception:
            hints = []
        if hints_path is None:
            self._cached_hints = hints
            by_file: dict[str, list[dict]] = {}
            for h in hints:
                by_file.setdefault(h["file"], []).append(h)
            self._cached_hints_by_file = by_file
        return hints

    def _hints_by_file(self, hints_path: str | Path | None = None) -> dict[str, list[dict]]:
        if self._cached_hints_by_file is not None and hints_path is None:
            return self._cached_hints_by_file
        hints = self.load_hints(hints_path)
        by_file: dict[str, list[dict]] = {}
        for h in hints:
            by_file.setdefault(h["file"], []).append(h)
        return by_file

    # ---- reporting ----
    def summary(self, filters=None) -> dict:
        lines = hit_l = br = hit_b = funcs = hit_f = 0
        for lns, brs, fns in self._sel(filters).values():
            lines += len(lns)
            hit_l += sum(1 for c in lns.values() if c > 0)
            br += len(brs)
            hit_b += sum(1 for c in brs.values() if c > 0)
            funcs += len(fns)
            hit_f += sum(1 for f_info in fns.values() if f_info.get("count", 0) > 0)

        hints_cov = self.hints_coverage(filters)

        return {
            "lines": lines,
            "lines_hit": hit_l,
            "branches": br,
            "branches_hit": hit_b,
            "functions": funcs,
            "functions_hit": hit_f,
            "line_pct": round(100 * hit_l / lines, 2) if lines else 0.0,
            "branch_pct": round(100 * hit_b / br, 2) if br else 0.0,
            "function_pct": round(100 * hit_f / funcs, 2) if funcs else 0.0,
            "hints_total": hints_cov.get("total_hints", 0),
            "hints_hit": hints_cov.get("hit_hints", 0),
            "hint_pct": hints_cov.get("hit_rate_pct", 0.0),
        }

    def hints_coverage(self, filters=None, hints_path=None) -> dict:
        """Detailed hit-rate statistics for static hints."""
        hints = self.load_hints(hints_path)
        filters = filters or DEFAULT_FILTERS
        lcov_data = self.lcov()

        total = 0
        instrumented = 0
        hit = 0
        missed = 0
        uninstrumented = 0

        by_kind: dict[str, dict] = {}
        by_subsystem: dict[str, dict] = {}

        for h in hints:
            fpath = h["file"]
            if not any(fpath.startswith(p) for p in filters):
                continue

            kind = h.get("kind", "unknown")
            sub_parts = fpath.split("/")
            subsystem = f"{sub_parts[0]}/{sub_parts[1]}" if len(sub_parts) > 1 else "other"

            total += 1
            if kind not in by_kind:
                by_kind[kind] = {"total": 0, "hit": 0, "miss": 0, "uninst": 0}
            if subsystem not in by_subsystem:
                by_subsystem[subsystem] = {"total": 0, "hit": 0, "miss": 0, "uninst": 0}

            by_kind[kind]["total"] += 1
            by_subsystem[subsystem]["total"] += 1

            entry = lcov_data.get(fpath)
            line = h.get("line", 0)
            line_count = entry[0].get(line) if entry else None

            if line_count is None:
                uninstrumented += 1
                by_kind[kind]["uninst"] += 1
                by_subsystem[subsystem]["uninst"] += 1
            else:
                instrumented += 1
                if line_count > 0:
                    hit += 1
                    by_kind[kind]["hit"] += 1
                    by_subsystem[subsystem]["hit"] += 1
                else:
                    missed += 1
                    by_kind[kind]["miss"] += 1
                    by_subsystem[subsystem]["miss"] += 1

        for k, v in by_kind.items():
            inst = v["hit"] + v["miss"]
            v["hit_pct"] = round(100 * v["hit"] / inst, 2) if inst else 0.0

        for k, v in by_subsystem.items():
            inst = v["hit"] + v["miss"]
            v["hit_pct"] = round(100 * v["hit"] / inst, 2) if inst else 0.0

        return {
            "total_hints": total,
            "instrumented_hints": instrumented,
            "hit_hints": hit,
            "missed_hints": missed,
            "uninstrumented_hints": uninstrumented,
            "hit_rate_pct": round(100 * hit / instrumented, 2) if instrumented else 0.0,
            "by_kind": by_kind,
            "by_subsystem": by_subsystem,
        }

    def uncovered_hints(self, filters=None, kind: str | None = None, top: int = 50, hints_path=None) -> list[dict]:
        """Ranked list of unexecuted static hints (sinks first, then branches, then constants)."""
        hints = self.load_hints(hints_path)
        filters = filters or DEFAULT_FILTERS
        lcov_data = self.lcov()

        kind_priority = {"sink": 0, "branch": 1, "magic_const": 2}
        uncovered = []

        for h in hints:
            fpath = h["file"]
            if not any(fpath.startswith(p) for p in filters):
                continue
            if kind and h.get("kind") != kind:
                continue

            entry = lcov_data.get(fpath)
            if not entry:
                continue
            line = h.get("line", 0)
            count = entry[0].get(line)
            if count == 0:  # instrumented but unexecuted!
                uncovered.append(h)

        uncovered.sort(key=lambda h: (kind_priority.get(h.get("kind"), 99), h["file"], h["line"]))
        return uncovered[:top]

    def uncovered_regions(self, filters=None, top: int = 20, gap: int = 2,
                          min_size: int = 4, context: int = 2, by: str = "size",
                          hints_path=None) -> list[Region]:
        """Clusters of consecutive uncovered lines, ranked by size, hints, or sinks."""
        regions: list[Region] = []
        hints_by_file = self._hints_by_file(hints_path)

        for f, (lns, brs, fns) in self._sel(filters).items():
            miss = sorted(n for n, c in lns.items() if c == 0)
            if not miss:
                continue
            src = None
            start = prev = miss[0]
            file_hints = hints_by_file.get(f, [])

            for n in miss[1:] + [10**9]:
                if n - prev > gap:
                    if prev - start + 1 >= min_size:
                        if src is None:
                            try:
                                src = (SRC / f).read_text(errors="replace").splitlines()
                            except OSError:
                                src = []
                        a, b = max(1, start - context), min(len(src), prev + context)
                        snippet = "\n".join(f"{i:5d} {src[i-1]}" for i in range(a, b + 1)) if src else ""
                        reg_hints = [h for h in file_hints if start <= h.get("line", 0) <= prev]
                        regions.append(Region(f, start, prev, snippet, reg_hints))
                    start = n
                prev = n

        if by == "hints":
            regions.sort(key=lambda r: (-r.hints_count, -r.size))
        elif by == "sinks":
            regions.sort(key=lambda r: (-r.sinks_count, -r.hints_count, -r.size))
        else:  # "size"
            regions.sort(key=lambda r: (-r.size, -r.hints_count))

        return regions[:top]

    def per_file(self, filters=None, top: int = 25):
        rows = []
        for f, (lns, _, _) in self._sel(filters).items():
            if lns:
                h = sum(1 for c in lns.values() if c > 0)
                rows.append((f, len(lns), h, 100 * h / len(lns)))
        rows.sort(key=lambda r: -(r[1] - r[2]))
        return rows[:top]

    def per_function(self, filters=None, top: int = 30, uncovered_only: bool = False, demangle: bool = True):
        """Ranked list of functions with line and execution count."""
        funcs = []
        for f, (_, _, fns) in self._sel(filters).items():
            for m_name, f_info in fns.items():
                cnt = f_info.get("count", 0)
                if uncovered_only and cnt > 0:
                    continue
                funcs.append((f, m_name, f_info.get("line", 0), cnt))

        if uncovered_only:
            funcs.sort(key=lambda x: (x[0], x[2]))
        else:
            funcs.sort(key=lambda x: (x[3], x[0], x[2]))

        selected = funcs[:top]
        if demangle and selected:
            names = [x[1] for x in selected]
            dm_map = demangle_batch(names)
            selected = [(f, dm_map.get(m, m), ln, c) for f, m, ln, c in selected]

        return selected

    def export_report(self, out_path: str | Path | None = None, filters=None) -> str:
        """Export comprehensive markdown coverage and hint-hit report."""
        out_p = Path(out_path or (self.dir / "REPORT.md"))
        out_p.parent.mkdir(parents=True, exist_ok=True)

        summ = self.summary(filters)
        hc = self.hints_coverage(filters)
        top_reg_hints = self.uncovered_regions(filters, top=15, by="hints")
        top_uncovered_hints = self.uncovered_hints(filters, top=20)
        top_uncovered_funcs = self.per_function(filters, top=20, uncovered_only=True)

        lines = [
            "# Coverage & Static Hint Cross-Reference Report (Task J3)",
            "",
            f"- **Target Binary:** `{BIN}`",
            f"- **Cumulative Profile:** `{self.profdata}`",
            f"- **Static Hints File:** `{DEFAULT_HINTS}`",
            "",
            "---",
            "",
            "## 1. Executive Summary",
            "",
            "| Metric | Total | Executed / Hit | Coverage Rate |",
            "|---|---|---|---|",
            f"| **Lines** | {summ['lines']:,} | {summ['lines_hit']:,} | **{summ['line_pct']:.2f}%** |",
            f"| **Branches** | {summ['branches']:,} | {summ['branches_hit']:,} | **{summ['branch_pct']:.2f}%** |",
            f"| **Functions** | {summ['functions']:,} | {summ['functions_hit']:,} | **{summ['function_pct']:.2f}%** |",
            f"| **Static Boundary Hints** | {hc['instrumented_hints']:,} | {hc['hit_hints']:,} | **{hc['hit_rate_pct']:.2f}%** |",
            "",
            "> [!IMPORTANT]",
            f"> Out of **{hc['instrumented_hints']:,}** instrumented static boundary hints across DuckDB core subsystems, "
            f"the baseline seed corpus covers only **{hc['hit_hints']:,} ({hc['hit_rate_pct']:.1f}%)**, "
            f"leaving **{hc['missed_hints']:,} hints uncovered** (including **{hc['by_kind'].get('sink', {}).get('miss', 0):,} critical assertion/memory sinks**). "
            "These uncovered hints form the explicit target set for directed LLM generation.",
            "",
            "---",
            "",
            "## 2. Static Hints Hit-Rate by Kind",
            "",
            "| Hint Kind | Total Hints | Instrumented | Hit | Missed | Hit Rate |",
            "|---|---|---|---|---|---|",
        ]

        for k, st in hc.get("by_kind", {}).items():
            inst = st["hit"] + st["miss"]
            lines.append(
                f"| `{k}` | {st['total']:,} | {inst:,} | {st['hit']:,} | {st['miss']:,} | **{st['hit_pct']:.1f}%** |"
            )

        lines.extend([
            "",
            "---",
            "",
            "## 3. Static Hints Hit-Rate by Subsystem",
            "",
            "| Subsystem | Total Hints | Instrumented | Hit | Missed | Hit Rate |",
            "|---|---|---|---|---|---|",
        ])

        for sub, st in sorted(hc.get("by_subsystem", {}).items()):
            inst = st["hit"] + st["miss"]
            lines.append(
                f"| `{sub}` | {st['total']:,} | {inst:,} | {st['hit']:,} | {st['miss']:,} | **{st['hit_pct']:.1f}%** |"
            )

        lines.extend([
            "",
            "---",
            "",
            "## 4. Top Uncovered Code Regions (Ranked by Hint Density)",
            "",
            "| File | Lines | Size | Enclosed Hints | Sinks |",
            "|---|---|---|---|---|",
        ])

        for r in top_reg_hints:
            lines.append(
                f"| `{r.file}` | L{r.start}-L{r.end} | {r.size} | **{r.hints_count}** | {r.sinks_count} |"
            )

        lines.extend([
            "",
            "---",
            "",
            "## 5. Prioritized Uncovered Hints for Directed LLM Prompts",
            "",
            "| File | Line | Kind | Target Value / Sink | Enclosing Function |",
            "|---|---|---|---|---|",
        ])

        for h in top_uncovered_hints:
            val_esc = h["value"].replace("|", "\\|")
            lines.append(
                f"| `{h['file']}` | {h['line']} | `{h['kind']}` | `{val_esc}` | `{h.get('function', 'global')}` |"
            )

        lines.extend([
            "",
            "---",
            "",
            "## 6. Sample Uncovered Functions",
            "",
            "| File | Line | Demangled Function |",
            "|---|---|---|",
        ])

        for f, fn, ln, _ in top_uncovered_funcs:
            fn_clean = fn.replace("|", "\\|")[:80]
            lines.append(f"| `{f}` | {ln} | `{fn_clean}` |")

        content = "\n".join(lines) + "\n"
        out_p.write_text(content)
        return content


def main() -> None:
    ap = argparse.ArgumentParser(description="llvm-cov ingestion, uncovered regions, and static hint cross-referencing.")
    sub = ap.add_subparsers(dest="cmd", help="Subcommand")

    # report
    p_rep = sub.add_parser("report", help="Print overall coverage summary and hint-hit rates")
    p_rep.add_argument("filters", nargs="*", default=DEFAULT_FILTERS, help="Subsystem path filters")

    # uncovered
    p_unc = sub.add_parser("uncovered", help="Rank uncovered regions")
    p_unc.add_argument("--by", choices=["size", "hints", "sinks"], default="hints", help="Ranking mode")
    p_unc.add_argument("--top", type=int, default=15, help="Number of regions to display")
    p_unc.add_argument("filters", nargs="*", default=DEFAULT_FILTERS, help="Subsystem path filters")

    # hints
    p_hnt = sub.add_parser("hints", help="Print uncovered static hints")
    p_hnt.add_argument("--kind", choices=["sink", "branch", "magic_const"], default=None, help="Hint kind filter")
    p_hnt.add_argument("--top", type=int, default=25, help="Number of hints to display")
    p_hnt.add_argument("filters", nargs="*", default=DEFAULT_FILTERS, help="Subsystem path filters")

    # functions
    p_fnc = sub.add_parser("functions", help="List per-function coverage")
    p_fnc.add_argument("--uncovered", action="store_true", help="Show only uncovered functions")
    p_fnc.add_argument("--top", type=int, default=20, help="Number of functions to display")
    p_fnc.add_argument("filters", nargs="*", default=DEFAULT_FILTERS, help="Subsystem path filters")

    # export-report
    p_exp = sub.add_parser("export-report", help="Export markdown coverage report")
    p_exp.add_argument("--out", default=str(WORK / "results" / "cov" / "REPORT.md"), help="Output markdown path")
    p_exp.add_argument("filters", nargs="*", default=DEFAULT_FILTERS, help="Subsystem path filters")

    # Legacy support: no subcmd but args like "report"
    if len(sys.argv) > 1 and sys.argv[1] not in ("report", "uncovered", "hints", "functions", "export-report", "-h", "--help"):
        args = ap.parse_args(["report"] + sys.argv[1:])
    else:
        args = ap.parse_args()

    c = Coverage()
    if not c.profdata.exists():
        print(f"Error: {c.profdata} does not exist. Run baseline fuzzing first.", file=sys.stderr)
        sys.exit(1)

    cmd = args.cmd or "report"
    flt = getattr(args, "filters", None) or DEFAULT_FILTERS

    if cmd == "report":
        s = c.summary(flt)
        hc = c.hints_coverage(flt)
        print("=== Coverage Summary ===")
        print(f"Lines:     {s['lines_hit']:7,d} / {s['lines']:<7,d} ({s['line_pct']:5.2f}%)")
        print(f"Branches:  {s['branches_hit']:7,d} / {s['branches']:<7,d} ({s['branch_pct']:5.2f}%)")
        print(f"Functions: {s['functions_hit']:7,d} / {s['functions']:<7,d} ({s['function_pct']:5.2f}%)")
        print("\n=== Static Hints Hit Rates ===")
        print(f"Overall:   {hc['hit_hints']:7,d} / {hc['instrumented_hints']:<7,d} ({hc['hit_rate_pct']:5.2f}%) hit, {hc['missed_hints']:,} missed")
        for k, st in hc["by_kind"].items():
            inst = st["hit"] + st["miss"]
            print(f"  {k:12s}: hit={st['hit']:5,d} / {inst:<5,d} ({st['hit_pct']:5.1f}%) | missed={st['miss']:5,d}")

        print("\n=== Top Files with Most Uncovered Lines ===")
        for f, n, h, p in c.per_file(flt, 10):
            print(f"{p:5.1f}%  {h:5d}/{n:<5d} {f}")

    elif cmd == "uncovered":
        regions = c.uncovered_regions(flt, top=args.top, by=args.by)
        print(f"=== Top {len(regions)} Uncovered Regions (ranked by {args.by}) ===")
        for i, r in enumerate(regions, 1):
            print(f"[{i}] {r.file}:L{r.start}-L{r.end} (size: {r.size}, hints: {r.hints_count}, sinks: {r.sinks_count})")
            if r.hints:
                for h in r.hints[:3]:
                    print(f"    -> [{h['kind']}] L{h['line']}: {h['value'][:70]}")

    elif cmd == "hints":
        hints = c.uncovered_hints(flt, kind=args.kind, top=args.top)
        kind_str = f" ({args.kind})" if args.kind else ""
        print(f"=== Top {len(hints)} Uncovered Static Hints{kind_str} ===")
        for i, h in enumerate(hints, 1):
            fn = h.get("function", "global")
            print(f"[{i:2d}] {h['file']}:{h['line']} [{h['kind']}] in {fn}")
            print(f"     Target: {h['value']}")

    elif cmd == "functions":
        funcs = c.per_function(flt, top=args.top, uncovered_only=args.uncovered)
        title = "Uncovered Functions" if args.uncovered else "Least-Covered Functions"
        print(f"=== Top {len(funcs)} {title} ===")
        for f, fn, ln, cnt in funcs:
            print(f"  {cnt:4d} hits | {f}:{ln:<4d} | {fn[:70]}")

    elif cmd == "export-report":
        out_path = c.export_report(args.out, flt)
        print(f"Report exported to: {args.out}")


if __name__ == "__main__":
    main()
