#!/usr/bin/env bash
# Start (or resume) a hunt detached from the terminal / Claude Code session, so it survives the session ending.
#   FUZZ_WORK=$HOME/cs5187 src/harness/hunt_start.sh --run hunt1 --hours 8 --max-llm-calls 60
# A supervisor restarts the loop after a crash (up to 5 times). Check progress: src/harness/hunt_status.sh RUN
# Resume after a stop (usage limit, reboot, kill): run the same command again with the same --run name; the ledger keeps
# retired targets, completed rounds and candidates. Stop it: kill -- -"$(cat $FUZZ_WORK/results/hunt/<run>/pid)"  (whole group).
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
# supervisor: restart after a crash (non-zero exit: an uncaught error, an OOM kill) up to 5 times; a normal finish or the clean
# stop after repeated LLM failures (usage limit) exits 0 and is NOT restarted. The ledger makes every restart a resume.
setsid nohup bash -c '
  tries=0
  while :; do
    "$0" "$1" "${@:2}"; rc=$?
    echo "$(date "+%F %T") hunt_loop exited rc=$rc" >> "'"$DIR"'/nohup.log"
    [ $rc -eq 0 ] && break
    tries=$((tries + 1)); [ $tries -ge 5 ] && { echo "$(date "+%F %T") giving up after $tries crashes" >> "'"$DIR"'/nohup.log"; break; }
    sleep "${HUNT_RESTART_SLEEP:-120}"
  done' "$PY" "$HERE/hunt_loop.py" "$@" >> "$DIR/nohup.log" 2>&1 < /dev/null &
echo $! > "$DIR/pid"
echo "started pid $(cat "$DIR/pid"); log: $DIR/RUN_LOG.md (and nohup.log); candidates: $DIR/CANDIDATES.md"
