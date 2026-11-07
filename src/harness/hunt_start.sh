#!/usr/bin/env bash
# Start (or resume) a hunt detached from the terminal / Claude Code session, so it survives the session ending.
#   FUZZ_WORK=$HOME/cs5187 src/harness/hunt_start.sh --run hunt1 --hours 8 --max-llm-calls 60
# Resume after a stop (usage limit, reboot, kill): run the same command again with the same --run name; the ledger keeps
# retired targets, completed rounds and candidates. Stop it: kill "$(cat $FUZZ_WORK/results/hunt/<run>/pid)".
# Laptop sleep pauses the run; wall-clock budgets keep counting and a unit that spans the sleep may time out (it is then
# logged as a timeout/artifact, not a finding).
set -euo pipefail
: "${FUZZ_WORK:?set FUZZ_WORK}"
RUN=""; prev=""
for a in "$@"; do [ "$prev" = "--run" ] && RUN="$a"; prev="$a"; done
[ -n "$RUN" ] || { echo "need --run NAME"; exit 2; }
DIR="$FUZZ_WORK/results/hunt/$RUN"; mkdir -p "$DIR"
if [ -f "$DIR/pid" ] && kill -0 "$(cat "$DIR/pid")" 2>/dev/null; then echo "already running: pid $(cat "$DIR/pid")"; exit 1; fi
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-$FUZZ_WORK/.venv/bin/python}"
cd "$HERE/.."
setsid nohup "$PY" "$HERE/hunt_loop.py" "$@" >> "$DIR/nohup.log" 2>&1 < /dev/null &
echo $! > "$DIR/pid"
echo "started pid $(cat "$DIR/pid"); log: $DIR/RUN_LOG.md (and nohup.log); candidates: $DIR/CANDIDATES.md"
