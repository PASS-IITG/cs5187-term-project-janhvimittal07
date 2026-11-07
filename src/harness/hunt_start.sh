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
rm -f "$DIR/pid"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-$FUZZ_WORK/.venv/bin/python}"
cd "$HERE/.."
# supervisor: restart after a crash (non-zero exit: an uncaught error, an OOM kill) up to 5 times; a normal finish or the clean
# stop after repeated LLM failures (usage limit) exits 0 and is NOT restarted. The ledger makes every restart a resume.
# keep Windows awake (no idle sleep) while the run lives: WSL cannot do this itself; closing the LID still sleeps unless
# Windows is set to "do nothing" on lid close. Skipped when not on WSL. Disable with HUNT_NO_KEEPAWAKE=1.
KA_ENC=""
if [ -z "${HUNT_NO_KEEPAWAKE:-}" ] && command -v powershell.exe >/dev/null 2>&1; then
  KA_ENC=$(iconv -f UTF-8 -t UTF-16LE "$HERE/keepawake.ps1" | base64 -w0)
fi
export KA_ENC HUNT_DIR="$DIR"
# the supervisor reads HUNT_DIR / KA_ENC from the environment (no nested quoting)
setsid nohup bash -c '
  echo $$ > "$HUNT_DIR/pid"              # the supervisor itself: session + process-group leader, so kill -- -PID stops everything
  ka=""
  if [ -n "$KA_ENC" ]; then powershell.exe -NoProfile -EncodedCommand "$KA_ENC" >> "$HUNT_DIR/keepawake.log" 2>&1 & ka=$!; fi
  cleanup() { [ -n "$ka" ] && kill "$ka" 2>/dev/null; }
  trap cleanup EXIT
  trap "echo \"\$(date +%F\ %T) stopped by signal\" >> \"\$HUNT_DIR/nohup.log\"; exit 143" TERM INT   # a deliberate stop is never restarted
  tries=0
  while :; do
    "$0" "$@"; rc=$?
    echo "$(date "+%F %T") hunt_loop exited rc=$rc" >> "$HUNT_DIR/nohup.log"
    [ $rc -eq 0 ] && break
    tries=$((tries + 1)); [ $tries -ge 5 ] && { echo "$(date "+%F %T") giving up after $tries crashes" >> "$HUNT_DIR/nohup.log"; break; }
    sleep "${HUNT_RESTART_SLEEP:-120}"
  done' "$PY" "$HERE/hunt_loop.py" "$@" >> "$DIR/nohup.log" 2>&1 < /dev/null &
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$DIR/pid" ] && kill -0 "$(cat "$DIR/pid")" 2>/dev/null && break; sleep 0.5; done
echo "started pid $(cat "$DIR/pid"); log: $DIR/RUN_LOG.md (and nohup.log); candidates: $DIR/CANDIDATES.md"
