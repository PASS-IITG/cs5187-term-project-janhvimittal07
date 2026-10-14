# Handoff: Short Hook: Plain Description of What Your Project Does and For Whom (FSE (CS-5187 Term Project)) — state on 2026-09-30

Read this first in a new session (human or Claude). Keep it current: it
replaces the conversation history.

## 1. The project in one paragraph

TODO: problem, what the tool does, main result so far.

## 2. Venue

| Item | Value |
|---|---|
| Venue / track | FSE (CS-5187 Term Project) |
| CFP | https://conf.researchr.org/home/fse-2026 (checked 2026-09-30) |
| Format | acmart acmsmall,screen,review,anonymous |
| Page limit | 12 pages main text |
| Deadline | TODO (see course page) AoE |
| Submission | TODO |

## 3. Where things are

* Paper: this directory (`sections/`, `macros.tex`, `figures/`, `tables/`).
* Tool: TODO path/repo.
* Raw data behind every number: `data/` (or the paths in §4).

## 4. Numbers in the paper and their sources

Reference environment: Ubuntu 24.04 LTS (x86_64, 20 vCPU, 7.4 GB RAM), Clang 18.1.3, CMake 3.28.3, Ninja 1.11.1, Python 3.12.3, DuckDB v1.5.6.

| Claim | Value | Source file |
|---|---|---|
| Total static hints extracted from DuckDB core | 24{,}816 | `paper/data/rq3_hints_summary.json` |
| Instrumented static hints | 24{,}065 | `paper/data/rq3_hints_summary.json` |
| Hints executed by baseline seed corpus | 4{,}587 (19.1\%) | `paper/data/rq3_hints_summary.json` |
| Hints unexecuted (uncovered) by seed corpus | 19{,}478 (80.9\%) | `paper/data/rq3_hints_summary.json` |
| Unexecuted program sinks (D_ASSERT, memcpy, casts) | 1{,}798 (78.5\% of sinks) | `paper/data/rq3_hints_by_kind.csv` |
| Unexecuted comparison branches | 9{,}780 (79.0\% of branches) | `paper/data/rq3_hints_by_kind.csv` |
| Unexecuted boundary constants | 7{,}900 (84.1\% of constants) | `paper/data/rq3_hints_by_kind.csv` |
| Core subsystems scanned (files / lines) | 818 files / 187{,}969 LOC | `src/static/APPROACH.md` |

## 5. Open decisions (ask the authors)

1. TODO

## 6. Paper TODOs

`grep -n '\\todo\|\\num{' sections/*.tex`

## 7. Lessons / pitfalls

* TODO
