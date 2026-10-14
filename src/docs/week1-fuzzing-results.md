# Week 1 Fuzzing Results

## Run configuration
- Seed sweep limit: 100
- Oracle sweep limit: 100
- ASan seed sweep and oracle sweep completed on 14 October 2026.

## Results observed in terminal
- Seed sweep: 100/100 seeds processed in approximately 5 seconds.
- Outcomes: 51 ok, 39 binder errors, 6 parse errors, 4 runtime errors.
- Oracle sweep: 100 seeds; 546 checks; 0 internal errors; 0 value mismatches; 16 error differences.
- Triage was interrupted manually during crash triage. The complete `fuzz.py all --limit 100` workflow therefore did not finish.

## Existing artifacts
- `~/fuzzwork/results/sweep-asan.jsonl`
- `~/fuzzwork/results/sweep-oracle.jsonl`
- `~/fuzzwork/results/REPORT.md`
- `~/fuzzwork/results/triage/`

## Interpretation and next steps
The sweeps completed, but the 16 error differences require triage. An error difference is not by itself proof of a DuckDB defect. Preserve the raw results and investigate the differences before reporting any as confirmed bugs.
