"""Run SQL against a DuckDB CLI build and classify the outcome.

Outcomes: ok, parse_error, binder_error, runtime_error, internal_error,
asan, crash, timeout. internal_error / asan / crash are the interesting ones.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

WORK = Path(os.environ.get("FUZZ_WORK", Path(__file__).resolve().parent.parent))  # duckdb/, build-*/, corpus/, results/
BUILDS = {
    "asan": WORK / "build-asan" / "duckdb",
    "cov": WORK / "build-cov" / "duckdb",
}

INTERESTING = {"internal_error", "asan", "crash", "timeout"}

_ERR_KINDS = [
    ("internal_error", re.compile(r"INTERNAL Error|Assertion failed|Assertion `")),
    ("parse_error", re.compile(r"Parser Error|Syntax error")),
    ("binder_error", re.compile(r"Binder Error|Catalog Error")),
    ("runtime_error", re.compile(r"(Conversion|Out of Range|Invalid Input|Constraint|"
                                 r"Invalid|Not implemented|Mismatch Type|Runtime|"
                                 r"Out of Memory|IO|Parser|Serialization|Dependency) Error")),
]
_ASAN = re.compile(r"ERROR: (Address|Leak|Undefined)Sanitizer|runtime error:|SUMMARY: \w+Sanitizer")


@dataclass
class Result:
    outcome: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    signature: str = ""          # dedupe key for interesting outcomes
    errors: list[str] = field(default_factory=list)

    @property
    def interesting(self) -> bool:
        return self.outcome in INTERESTING


def _frames(text: str, n: int = 4) -> list[str]:
    """Top n function names from an ASan / backtrace dump."""
    out = []
    for m in re.finditer(r"#\d+ 0x[0-9a-f]+ in ([^\s(]+)", text):
        out.append(m.group(1))
        if len(out) == n:
            break
    return out


def _signature(outcome: str, text: str) -> str:
    if outcome == "internal_error":
        m = re.search(r"INTERNAL Error: ([^\n]{0,160})", text) or re.search(r"(Assertion[^\n]{0,160})", text)
        key = re.sub(r"\d+", "N", m.group(1)) if m else text[:120]
    elif outcome in ("asan", "crash"):
        key = "|".join(_frames(text)) or text[:120]
    else:
        key = outcome
    return f"{outcome}:{hashlib.sha1(key.encode()).hexdigest()[:12]}"


def classify(returncode: int, stdout: str, stderr: str) -> Result:
    text = stderr + "\n" + stdout
    if _ASAN.search(text):
        outcome = "asan"
    elif returncode < 0 or returncode in (134, 139):       # signalled / abort / segv
        outcome = "crash"
    else:
        outcome = "ok"
        for kind, rx in _ERR_KINDS:
            if rx.search(text):
                outcome = kind
                break
        # an INTERNAL error anywhere beats a benign error earlier in the batch
        if outcome != "internal_error" and _ERR_KINDS[0][1].search(text):
            outcome = "internal_error"
    r = Result(outcome, returncode, stdout, stderr)
    r.errors = re.findall(r"^\w+ Error: .*$", text, flags=re.M)
    if r.interesting:
        r.signature = _signature(outcome, text)
    return r


def run_sql(sql: str, build: str = "asan", timeout: float = 30.0,
            db: str | None = None, cwd: Path | None = None,
            env_extra: dict | None = None) -> Result:
    """Run a SQL script (many statements) in one CLI process."""
    exe = BUILDS[build]
    if not exe.exists():
        raise FileNotFoundError(f"{exe} not built yet")
    env = dict(os.environ)
    env.setdefault("ASAN_OPTIONS", "detect_leaks=0:abort_on_error=0:symbolize=1")
    env.setdefault("UBSAN_OPTIONS", "print_stacktrace=1")
    env.update(env_extra or {})
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as f:
        f.write(sql)
        path = f.name
    try:
        p = subprocess.run([str(exe), db or ":memory:", "-init", "/dev/null", "-f", path],
                           capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd,
                           errors="replace")
        return classify(p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired as e:
        return Result("timeout", -1, (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                      "timeout", signature="timeout:" + hashlib.sha1(sql.encode()).hexdigest()[:12])
    finally:
        Path(path).unlink(missing_ok=True)


def split_statements(sql: str) -> list[str]:
    """Cheap splitter (no DuckDB needed); good enough for LLM output."""
    stmts, cur, q = [], [], None
    for ch in sql:
        cur.append(ch)
        if q:
            if ch == q:
                q = None
        elif ch in ("'", '"'):
            q = ch
        elif ch == ";":
            s = "".join(cur).strip()
            if s != ";":
                stmts.append(s)
            cur = []
    tail = "".join(cur).strip()
    if tail:
        stmts.append(tail)
    return stmts


def find_culprit(setup: str, stmts: list[str], build: str = "asan", timeout: float = 30.0) -> tuple[int, Result] | None:
    """After a batch crash, re-run prefix + one statement to locate the trigger."""
    for i, s in enumerate(stmts):
        r = run_sql(setup + "\n" + s, build, timeout)
        if r.interesting:
            return i, r
    return None
