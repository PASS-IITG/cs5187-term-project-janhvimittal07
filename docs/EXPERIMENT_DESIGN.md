# Experiment design: directed LLM fuzzing of DuckDB 2.0.0 (for the paper)

Define the comparison **before** running anything, so the result is not fitted afterwards.

## Research questions
* **RQ1** Does aiming the LLM at code that is new in 2.0 (changed lines + feature cards) beat unguided LLM generation, on
  measurable engine behaviour?
* **RQ2** Does engine-measured feedback (valid rate, plan-change "reach", plan shapes) improve an LLM-written generator
  from one iteration to the next?
* **RQ3** Do LLM-written generators (one program -> thousands of cases) find more than hand-written ones and than generic
  fuzzers (SQLsmith-style, mutated seeds)?
* **RQ4** Fix-diff variant hunting: do dossiers of already-fixed bugs yield siblings / incomplete fixes on 2.0.0?

## Arms
| Arm | What |
|---|---|
| A1 generic | SQLsmith campaign + mutated seeds + function calls (already run: ~700k queries, 0 new) |
| A2 hand-written targeted | `fuzz_triggers.py`, `fuzz_joins.py`, `fuzz_windows.py`, `bottomup.py` (human authors) |
| A3 LLM unguided | LLM writes SQL / generators with no target and no feedback (`generate.py --targets none`) |
| A4 LLM directed | new-code hints + feature cards + engine feedback loop + fix dossiers (this work) |

## Metrics (all measurable on the release build; no coverage build needed)
valid-query rate; **reach rate** per target (EXPLAIN operator sequence changes when the target optimizer / setting is switched
off); distinct plan shapes; distinct INTERNAL signatures; **verified** bugs (reproduced alone, not fixed in the newer snapshot,
nothing upstream) and **candidate-new** bugs separately; number of model calls and which model; wall time. Report counts, not
percentages of percentages; one bug is an anecdote, say so.

## Threats to state honestly
Few bugs => no statistical power; upstream moves daily (a bug can be reported while we verify it); reach rate is a proxy for
coverage; LLM output depends on the model and temperature (record both); several arms were run on different days.

## Coverage of changed lines (the original proposal promised it)
Needs `build-cov` rebuilt from v2.0.0 (the current one is 1.5.6): `FUZZ_WORK=... JOBS=3 bash src/build.sh cov`, about an hour,
nothing else heavy running. Decide with the team whether the paper needs the number; if yes, schedule it as its own job and measure
A2 / A3 / A4 against it.
