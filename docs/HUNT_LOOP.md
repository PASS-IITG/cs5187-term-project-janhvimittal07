# Rule-targeted hunt loop (automated)

This is the method that found upstream issues 28531, 28532 and 28534 (and an unfiled UBIGINT bug) on 2026-11-07, turned into a
loop that runs without a person in it. A person still reads every candidate and decides whether to file.

## The method

1. **Target from code and history** (`harness/rule_targets.py`). Each optimizer rule or optimizer gets a card with:
   - its recent fix commits, and the test files those fixes added. A fix usually covers only the case that was reported; the
     cases next to it are where the next bug is.
   - every early exit in `Apply()`/`CanOptimize()`, i.e. the conditions under which the rewrite fires;
   - every place where it builds or converts a value (bounds, folded constants, casts);
   - its marker functions, and the switch that turns it off.
2. **Hypothesis first.** The LLM reads the source and must state `META["hypothesis"]` and `META["preconditions"]`. The judge
   rejects a rule-targeted generator that leaves them out.
3. **Generator recipes that worked:**
   - constants taken from the rows (so `=` can match) and from `EDGES` (`harness/edge_values.py`: 275 literals at the limits of
     30 types);
   - the target pattern in varied places (WHERE, SELECT, CASE, FILTER, JOIN ON, subquery);
   - deterministic queries (unique window order keys, no unordered LIMIT).
4. **The engine judges** (`gen_runner.py`): 5 configurations, rule-level reach (the marker call disappears from the plan),
   plan-shape diversity. "Only the optimized configuration errors" is reported too.
5. **Every hit is triaged** (`gen_triage.py`), whatever the quality verdict: verify, known-issues registry, delta-minimize,
   newest main nightly (`nightly.py`), upstream search. Hits where the optimizer only *hides* an error are labelled low priority.

## Running it

```bash
export FUZZ_WORK=$HOME/cs5187; PY=$FUZZ_WORK/.venv/bin/python
$PY src/harness/rule_targets.py build                                     # cards -> $FUZZ_WORK/results/targeted/rule_cards/
$PY src/harness/controls/check_controls.py --rediscovery                  # must print CONTROLS PASSED (about 25 min)
src/harness/hunt_start.sh --run hunt1 --hours 8 --max-llm-calls 60      # detached; LLM = `claude -p --model opus` (your plan quota)
```

**Interruptions.** The run is detached (`setsid nohup`), so it survives the terminal or Claude Code session ending.
- Usage limit / network: a failed LLM call is not counted. The loop backs off, and after 4 failures in a row it stops
  cleanly *without* retiring targets.
- Kill / reboot: a round is only counted when it completes.
- To resume, run the same command with the same `--run`: `ledger.json` keeps retired targets, completed rounds and candidates.
- Idle sleep: on WSL the launcher keeps Windows awake (`keepawake.ps1`, SetThreadExecutionState) while the run lives. Closing
  the LID still sleeps unless Windows is set to "Do nothing" on lid close. If it sleeps anyway, a unit that spans the sleep
  may time out and is then logged as a timeout or artifact, never as
  a finding.

Results go to `$FUZZ_WORK/results/hunt/<run>/`:
- `RUN_LOG.md`: one line per unit;
- `ledger.json`: per-target state, so a stopped run resumes;
- `CANDIDATES.md`: minimized reproducers with main-nightly and upstream-search results;
- `llm/`: every prompt and response;
- `gens/`, `triage/`: per-generator results.

## Safety and honesty rules built in

- The LLM runs `claude -p` with **read-only** tools (Read, Grep, Glob) on the DuckDB source only. Its working directory is
  an empty folder, so it never sees the repo, the issue drafts, the controls or `generators-quarantine/`.
- One worker, `nice`, DuckDB `memory_limit`, and a free-RAM check before every unit (pauses below 1.5 GB). Use pip only with
  `--only-binary`.
- Read-only GitHub calls. **Nothing is filed or pushed.** `CANDIDATES.md` is a to-read list, not a list of bugs.
- Controls (`src/harness/controls/`) are excluded from the audit's generator counts (`author_model` `control:` /
  `human-control`). The session generators that found the bugs live there as rediscovery controls.

## Known limits (state them in the paper)

- Three of the four pipeline finds came from generators the quality gate rejects (narrow, deep generators have few plan
  shapes). The loop therefore triages hits from all generators; the gate decides what counts as tested coverage.
- The finds so far are mostly one family (constants at type limits in optimizer rewrites). Expect diminishing returns per
  target; rotating targets is the answer, not longer runs on one.
- Rules that match operators (`+`, LIKE) have no marker function, so their reach is measured only at expression level, which
  is weak evidence.
