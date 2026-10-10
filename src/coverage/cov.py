"""llvm-cov ingestion: cumulative merge, lcov export, uncovered-region report.

Usage (library):
    c = Coverage("results/cov")                 # work dir
    c.run_and_merge(sql_text)                   # run on build-cov, fold into cumulative profile
    print(c.summary(["src/optimizer", "src/execution"]))
    for r in c.uncovered_regions(["src/optimizer"], top=10): print(r.file, r.start, r.end)

CLI:  python coverage/cov.py report [dir-prefix ...]
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("FUZZ_WORK", Path(__file__).resolve().parent.parent))  # duckdb/, build-*/, corpus/, results/
sys.path.insert(0, str(CODE / "harness"))
import runner  # noqa: E402

BIN = WORK / "build-cov" / "duckdb"
SRC = WORK / "duckdb"
DEFAULT_FILTERS = ["src/optimizer", "src/planner", "src/execution", "src/function",
                   "src/parser", "src/storage", "src/common"]


@dataclass
class Region:
    file: str
    start: int
    end: int
    snippet: str

    @property
    def size(self) -> int:
        return self.end - self.start + 1


class Coverage:
    def __init__(self, workdir: str | Path = WORK / "results" / "cov"):
        self.dir = Path(workdir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.profdata = self.dir / "all.profdata"
        self._lcov: dict[str, tuple[dict[int, int], dict[tuple, int]]] | None = None

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
    def lcov(self):
        if self._lcov is not None:
            return self._lcov
        out = subprocess.run(
            ["llvm-cov", "export", str(BIN), f"-instr-profile={self.profdata}",
             "-format=lcov", "-ignore-filename-regex=third_party|/usr/"],
            check=True, capture_output=True, text=True).stdout
        files: dict[str, tuple[dict[int, int], dict[tuple, int]]] = {}
        cur = None
        for ln in out.splitlines():
            if ln.startswith("SF:"):
                p = ln[3:]
                try:
                    p = str(Path(p).resolve().relative_to(SRC))
                except ValueError:
                    pass
                cur = files.setdefault(p, ({}, {}))
            elif ln.startswith("DA:") and cur is not None:
                n, c = ln[3:].split(",")[:2]
                cur[0][int(n)] = max(cur[0].get(int(n), 0), int(c))
            elif ln.startswith("BRDA:") and cur is not None:
                n, blk, br, c = ln[5:].split(",")
                k = (int(n), blk, br)
                cur[1][k] = max(cur[1].get(k, 0), 0 if c == "-" else int(c))
        self._lcov = files
        return files

    # ---- reporting ----
    def _sel(self, filters):
        filters = filters or DEFAULT_FILTERS
        return {f: v for f, v in self.lcov().items() if any(f.startswith(p) for p in filters)}

    def summary(self, filters=None) -> dict:
        lines = hit_l = br = hit_b = 0
        for lns, brs in self._sel(filters).values():
            lines += len(lns)
            hit_l += sum(1 for c in lns.values() if c)
            br += len(brs)
            hit_b += sum(1 for c in brs.values() if c)
        return {"lines": lines, "lines_hit": hit_l, "branches": br, "branches_hit": hit_b,
                "line_pct": round(100 * hit_l / lines, 2) if lines else 0.0,
                "branch_pct": round(100 * hit_b / br, 2) if br else 0.0}

    def uncovered_regions(self, filters=None, top: int = 20, gap: int = 2,
                          min_size: int = 4, context: int = 2) -> list[Region]:
        """Clusters of consecutive uncovered lines, largest first."""
        regions: list[Region] = []
        for f, (lns, brs) in self._sel(filters).items():
            miss = sorted(n for n, c in lns.items() if c == 0)
            if not miss:
                continue
            src = None
            start = prev = miss[0]
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
                        regions.append(Region(f, start, prev, snippet))
                    start = n
                prev = n
        regions.sort(key=lambda r: -r.size)
        return regions[:top]

    def per_file(self, filters=None, top: int = 25):
        rows = []
        for f, (lns, _) in self._sel(filters).items():
            if lns:
                h = sum(1 for c in lns.values() if c)
                rows.append((f, len(lns), h, 100 * h / len(lns)))
        rows.sort(key=lambda r: -(r[1] - r[2]))
        return rows[:top]


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "report":
        c = Coverage()
        flt = sys.argv[2:] or None
        print(c.summary(flt))
        for f, n, h, p in c.per_file(flt, 15):
            print(f"{p:5.1f}%  {h:5d}/{n:<5d} {f}")
    else:
        print(__doc__)
