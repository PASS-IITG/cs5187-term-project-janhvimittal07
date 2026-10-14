#!/usr/bin/env bash
set -u

export FUZZ_WORK="${FUZZ_WORK:-$HOME/fuzzwork}"
PY="$FUZZ_WORK/.venv/bin/python"
mkdir -p docs "$FUZZ_WORK/results"

echo "=== Running required Week 1 fuzzing task ==="
"$PY" harness/fuzz.py all --limit 100
FUZZ_STATUS=$?
echo "Fuzzing exit status: $FUZZ_STATUS"

echo "=== Comparing saved reproducers on pinned main ==="
"$PY" - <<'PY'
import json, os, subprocess
from pathlib import Path

root = Path(os.environ["FUZZ_WORK"])
repo = Path.cwd()
py = root / ".venv/bin/python"
checker = repo / "harness/main_triage.py"
triage = root / "results/triage"
# Take the list first so newly created checker output is not reprocessed.
files = sorted(triage.glob("*/repro.sql"))
results = []

for sql in files:
    p = subprocess.run([str(py), str(checker), str(sql)],
                       capture_output=True, text=True)
    try:
        result = json.loads(p.stdout)
    except json.JSONDecodeError:
        result = {"finding_id": sql.parent.name,
                  "error": (p.stderr or p.stdout or "checker failed")[-1000:]}
    results.append(result)
    print(json.dumps(result))

(root / "results/WEEK1_MAIN_TRIAGE.json").write_text(
    json.dumps(results, indent=2) + "\n", encoding="utf-8")

report = root / "results/REPORT.md"
if report.exists():
    text = report.read_text(encoding="utf-8")
    marker = "## Week 1: Pinned-main comparison"
    if marker in text:
        text = text[:text.index(marker)].rstrip()
    lines = [
        "", "", "## Week 1: Pinned-main comparison", "",
        "Automatic classification is a triage signal, not proof of a defect.",
        "", "| Finding | ASan | Pinned main | Reproduces on main? |",
        "|---|---|---|---|"
    ]
    for r in results:
        if "error" in r:
            lines.append(f"| {r.get('finding_id','unknown')} | unknown | unknown | unknown |")
        else:
            lines.append(
                f"| {r.get('finding_id','unknown')} | "
                f"{r.get('asan_outcome','unknown')} | "
                f"{r.get('main_outcome','unknown')} | "
                f"{r.get('reproduces_on_main','unknown')} |")
    lines += ["", f"Compared {len(results)} saved reproducer(s).",
              "Detailed metadata and logs are stored under `~/fuzzwork/results/triage/`."]
    report.write_text(text + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"Updated {report}")
else:
    print("REPORT.md not found; comparison JSON was still saved.")
PY
COMPARE_STATUS=$?

echo "=== Writing N1 build documentation ==="
cat > docs/week1-n1-validation.md <<'DOC'
# Week 1 — N1: Build configuration validation

## Verified flags

- `build-asan`: RelWithDebInfo, ASan enabled, UBSan enabled, assertions enabled, frame pointers retained.
- `build-cov`: RelWithDebInfo, assertions enabled, sanitizers disabled, LLVM `-fprofile-instr-generate -fcoverage-mapping` coverage flags.
- Builds use Ninja with four jobs and the ASan and coverage variants are built sequentially.

## Existing executable sizes

- ASan executable: approximately 156 MiB.
- Coverage executable: approximately 136 MiB.
- Pinned-main executable: approximately 215 MiB.

Executable size is not RAM usage. The original full-build duration and peak memory were not recorded in the available evidence. The build script's approximate RAM target is not a measured peak; these measurements are therefore reported as unavailable, not estimated.
DOC

cat > docs/week1-n2-validation.md <<'DOC'
# Week 1 — N2: Pinned-main validation

Pinned DuckDB revision: `690d113c24dad3b37529c1518354bcbe00d8ffbc`.

The pinned build script verified the exact revision and the `SELECT 1 + 1` smoke test returned `2`. The standalone checker compares saved SQL reproducers against the ASan build and pinned-main build, saving JSON metadata and stdout/stderr logs.

An initial comparison returned `binder_error` on both builds and recorded `reproduces_on_main = false`. This is not proof of a confirmed bug or a fix. Per-finding results are collected in `~/fuzzwork/results/WEEK1_MAIN_TRIAGE.json`; the report is updated when `REPORT.md` exists.
DOC

echo "=== Committing and pushing Week 1 deliverables ==="
git add harness/week1_finalize.sh docs/week1-n1-validation.md docs/week1-n2-validation.md
git add "$FUZZ_WORK/results/REPORT.md" 2>/dev/null || true
git commit -m "Complete Week 1 build and pinned-main validation"
git pull --rebase origin main && git push origin main

echo "Fuzzing status: $FUZZ_STATUS"
echo "Comparison step status: $COMPARE_STATUS"
echo "If fuzzing returned nonzero, preserve its output for follow-up."
