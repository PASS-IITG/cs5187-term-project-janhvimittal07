#!/usr/bin/env bash
# Progress of a hunt: is it alive, what did it do last, what has it found.   usage: src/harness/hunt_status.sh RUN
: "${FUZZ_WORK:=$HOME/cs5187}"
DIR="$FUZZ_WORK/results/hunt/${1:?run name}"
if [ -f "$DIR/pid" ] && kill -0 "$(cat "$DIR/pid")" 2>/dev/null; then echo "RUNNING (supervisor pid $(cat "$DIR/pid"))"; else echo "NOT RUNNING"; fi
echo "free RAM: $(awk '/MemAvailable/ {printf "%.1f GB", $2/1048576}' /proc/meminfo)"
echo "--- last log lines"; tail -n 8 "$DIR/RUN_LOG.md" 2>/dev/null
echo "--- ledger"
python3 - "$DIR/ledger.json" <<'PY'
import json, sys
try:
    l = json.load(open(sys.argv[1]))
except Exception as e:
    print("no ledger yet", e); sys.exit()
t = l["targets"]
print(f"LLM calls {l['llm_calls']}, targets retired {sum(v['status'] == 'retired' for v in t.values())}, active {sum(v['status'] != 'retired' for v in t.values())}, candidates {len(l['candidates'])}")
for c in l["candidates"]:
    print(f"  {c['cls']:14} {c['target']:32} {c['kind']:12} main={str((c.get('main') or {}).get('status'))} {str(c.get('signature'))[:60]}")
PY
echo "--- crashes/restarts"; grep -c "exited rc=[1-9]" "$DIR/nohup.log" 2>/dev/null || echo 0
