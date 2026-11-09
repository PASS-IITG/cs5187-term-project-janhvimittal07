#!/bin/bash
# Replay ONE batch of hand-written (LLM-authored) SQL scripts and print a summary. One heavy job at a time (flock).
#   direct_replay.sh $FUZZ_WORK/corpus/llm/direct/b01      (dir of self-contained .sql scripts)
# Stages: (1) wheel differential sweep (default vs optimizers off, NoREC/TLP, ...), (2) ASan+UBSan replay (plain + enable_verification),
#         (3) changed-line coverage of this batch alone and its GAIN over the generic baseline A1.
set -u
export FUZZ_WORK=${FUZZ_WORK:-/home/giraffe/cs5187}
PY=$FUZZ_WORK/.venv/bin/python; H=~/term-project/src/harness; COV=~/term-project/src/coverage/cov.py
B=$1; N=$(basename "$B"); R=$FUZZ_WORK/results/targeted/direct-run/$N; mkdir -p "$R"
exec 9>/tmp/fuzz-heavy.lock; flock 9
cd $FUZZ_WORK/work-scratch
echo "n_scripts $(ls "$B"/*.sql | wc -l)" > "$R/SUMMARY.txt"
nice $PY $H/sweep_oracle.py --seeds "$B" --out "$R/sweep.jsonl" --metamorphic --workers 2 > "$R/sweep.log" 2>&1
echo "sweep: $(tail -n 1 "$R/sweep.log" | cut -c1-200)" >> "$R/SUMMARY.txt"
n_asan() { ps -eo cmd | grep -c "^[^ ]*/python .*harness/asan_replay.py"; }   # real python processes only (shell wrappers also contain the text)
while [ "$(n_asan)" -ge 2 ]; do sleep 30; done
ASAN_OPTIONS=detect_leaks=0:hard_rss_limit_mb=2500 nice $PY $H/asan_replay.py --sql-dir "$B" --out "$R/asan" > "$R/asan.log" 2>&1
echo "asan: $(tail -n 1 "$R/asan.log" | cut -c1-200)" >> "$R/SUMMARY.txt"
nice $PY $COV run-dir "$B" --workdir "$R/cov" --timeout 60 > "$R/cov.log" 2>&1
mkdir -p "$R/cov-merged"; llvm-profdata merge -sparse -o "$R/cov-merged/all.profdata" $FUZZ_WORK/results/cov-A1/all.profdata "$R/cov/all.profdata" 2>>"$R/cov.log"
$PY $COV changed --workdir "$R/cov" --json "$R/cov/changed.json" >> "$R/cov.log" 2>&1
$PY $COV changed --workdir "$R/cov-merged" --json "$R/cov-merged/changed.json" >> "$R/cov.log" 2>&1
$PY - "$R" "$FUZZ_WORK" >> "$R/SUMMARY.txt" <<'PYEOF'
import json,sys
R,W=sys.argv[1:3]
try:
    a=json.load(open(W+"/results/cov-A1/changed.json")); b=json.load(open(R+"/cov/changed.json")); m=json.load(open(R+"/cov-merged/changed.json"))
    print(f"coverage: batch alone {b['covered']} changed lines ({b['pct']}%); GAIN over A1: +{m['covered']-a['covered']} lines")
    for k in a["by_subsystem"]:
        d=m["by_subsystem"][k]["covered"]-a["by_subsystem"][k]["covered"]
        if d: print(f"   {k}: +{d}")
except Exception as e: print("coverage: failed", e)
PYEOF
echo "hits: asan=$(grep -c . "$R/asan/hits.jsonl" 2>/dev/null || echo 0) sweep_found=$(grep -c '"found": \[{' "$R/sweep.jsonl" 2>/dev/null || echo 0)" >> "$R/SUMMARY.txt"
cat "$R/SUMMARY.txt"
