# Handoff: Semantic LLM-Directed Fuzzing of DuckDB (CS-5187 term project, FSE format) — state on 2026-11-13

## 1. The project in one paragraph

SLDF is a framework of four fuzzing methods for DuckDB (A classic, B feature fuzzers, C hints + LLM generators,
D rule-targeted loop) that share one engine judge, one oracle layer and one triage pipeline. It found 16 previously
unknown bugs in DuckDB 2.0.0 (8 silent wrong results); 4 are fixed upstream and 9 more have open fix PRs.

## 2. Venue

| Item | Value |
|---|---|
| Venue / track | FSE format (CS-5187 term project) |
| Format | acmart acmsmall,screen (names shown, not anonymous: course report) |
| Page limit | 12 pages main text |
| Deadline | Fri 13 Nov 2026, 23:59 IST (docs/PROJECT_GUIDE.pdf) |
| Submission | Overleaf link, PDF, public GitHub repo, reproduction instructions (src/README.md) |

## 3. Where things are

* Paper: this directory. Tool: `src/harness`. Raw data behind every number: `data/` (regenerate with
  `scripts/collect_data.py` then `scripts/collect_extra.py`; needs `FUZZ_WORK` and `gh`).

## 4. Numbers in the paper and their sources

Environment: Intel i3-1315U, 8 threads, 7.4 GB RAM, WSL2 Linux 6.18, Python 3.14.4, DuckDB 2.0.0, Claude Code 2.1.323.

| Claim | Value | Source file |
|---|---|---|
| Issues filed; by kind | 16; 8 wrong, 5 internal, 3 spurious error | `data/summary.json` (by_kind), `data/issues.csv` |
| By method | A 0, B 1, C 3, D 12 (5 session + 7 loop) | `data/summary.json` (by_method) |
| Fixed upstream | 4: #27759 (PR 28698), #27850 (PR 28144), #28052 (PR 28071), #28562 (PR 28563) | `data/issues.csv` (live GitHub, 2026-11-13) |
| Open with a fix PR | 9 (one ours, #28547) | `data/summary.json` (open_with_fix_pr) |
| Labelled reproduced | 11 | `data/summary.json` |
| hunt1: calls, targets, rounds, accepted | 40, 13 (12 ran), 32, 23 | `data/summary.json` (hunt1), `data/hunt_units.csv` |
| hunt1: raw hits, triaged, candidates, issues | 597, 138, 9, 6 | `tables/tab_hunt1.tex`, `data/extra.json` |
| hunt1 triage classes | dup 56, fixed on main 54, low 10, rediscovery 8, artifact 1, new 9 | `data/extra.json` |
| Controls | cheat rule reach 0.00 vs expr 0.23; 4 session generators re-find 28531/28532/28534/28535 | `data/summary.json` (controls), `data/extra.json` |
| Method C Round 2 (Gemini) | 12 generators, 36,600 cases, 122,590 valid queries, cov 17.89% vs 38.64% | `src/results/targeted/FINAL_SUMMARY.md`, commit 3f2a9ca message |
| Method C pilot (1.5B) | 72/126 valid, 0/10 targets | `docs/PROJECT_GUIDE.pdf` §6.1 |
| Method A | SQLsmith 70,980 queries (92.3% valid, 46 raw anomalies); sweep 11,235 queries; 14,000 ASan runs, 109 hits, 22 signatures | `$FUZZ_WORK/results/campaign-sqlsmith/CAMPAIGN_REPORT.md`, `docs/PROJECT_GUIDE.pdf` §6.1 |
| Static hints | 24,816; 80.9% unexecuted | `data/rq3_hints_summary.json` |
| DuckDB 2.0.0 size | 46 optimizer passes, 29 rule files, 49 rule cards | `duckdb_optimizers()`, `src/optimizer/rule/`, rule_cards/ |

## 5. Open decisions (ask the authors)

1. Each author confirms the Author Contributions and Use of Generative AI paragraphs (sections/data.tex).
2. References are written from memory without DOIs: check each against the publisher page.
3. Compile on Overleaf and check the page limit (no local LaTeX on the laptop).

## 6. Lessons / pitfalls

* Issue states change daily: rerun `collect_data.py` before the final PDF.
* GitHub cross-references include PRs that only mention an issue; the fix PRs of #28535/#28541 are pinned in the script.
