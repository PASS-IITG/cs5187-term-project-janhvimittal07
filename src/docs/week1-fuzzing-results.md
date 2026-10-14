# Week 1 Fuzzing Results

## Run configuration
- Seed sweep limit: 100
- Oracle sweep limit: 100
- ASan seed sweep and oracle sweep completed on 14 October 2026.

## Results observed in terminal
- **Seed sweep:** 100/100 seeds processed in 4 seconds (`~/fuzzwork/results/sweep-asan.jsonl`).
  - Outcomes: 51 ok, 39 binder errors, 6 parse errors, 4 runtime errors, **0 crashes**.
- **Oracle sweep:** 100 seeds; 546 differential checks across 5 configurations in 3 seconds (`~/fuzzwork/results/sweep-oracle.jsonl`).
  - Outcomes: 0 internal errors, 0 value mismatches, 16 error differences.
- **Triage stage:** **Completed end-to-end.**
  - All 16 error differences from `test_bitstring_agg.sql` triaged and deduplicated.
  - Baseline timeout candidates (`timeout_7e2d255998c4.sql`, `timeout_a24df23746e5.sql`) verified and triaged.
  - All findings verified against `build-main/duckdb` via `_check_on_main()`, recording `reproduces_on_main = false` for benign differences.
- **Report stage:** Final summary table rendered with the `main build` verification column in `~/fuzzwork/results/REPORT.md`.

## Workflow status
The complete `python harness/fuzz.py all --limit 100` pipeline executed from start to finish without interruption in **610 seconds** (~10 minutes). All stages (`seeds`, `sweep`, `baseline`, `triage`, `report`) succeeded cleanly.

## Existing artifacts
- `~/fuzzwork/results/sweep-asan.jsonl`
- `~/fuzzwork/results/sweep-oracle.jsonl`
- `~/fuzzwork/results/REPORT.md`
- `~/fuzzwork/results/triage/`
- `~/fuzzwork/results/known.json`

## Interpretation & Next Steps
The end-to-end pipeline operates reliably. The 16 error differences were caused by disabled optimizer passes (`statistics_propagation`) altering the timing of `BITSTRING_AGG` errors between bind-time and execution-time, which are now properly handled by N8 noise gates. The pipeline is validated and ready for the LLM generator (P2) and static hints (J2) in Week 2.

