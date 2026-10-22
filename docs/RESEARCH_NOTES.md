# Research notes: how others find DBMS (and DuckDB) bugs

Collected 2026-10-22 while hunting on DuckDB 2.0.0. Facts marked **(read)** come from the paper text itself;
**(snippet)** means only a search-result summary was seen and should be checked before it goes into the paper.

## Papers and what to take from them

| Work | Idea | DuckDB result | What it means for us |
|---|---|---|---|
| **SQLBull**, Liang & Liu, NDSS 2026 **(read)** | Bottom-up generation: start from a feature-rich grammar rule (POSITIONAL JOIN, LATERAL, correlated subquery) and build upward to a root; merge several paths. | 63 zero-days over 5 DBMSs; DuckDB tested at v1.1.x. Example: POSITIONAL JOIN over a LATERAL subquery -> Internal Fatal Error. | Implemented as `harness/bottomup.py`. That PoC is now rejected cleanly by 2.0.0 ("Positional join in correlated subqueries is not supported"). |
| **SmartFuzz**, Lin et al., OOPSLA 2026 **(read)** | LLM synthesises seeds from (1) official docs features and (2) *historical crash reports*, recombined ("crash-reuse"), then mutation fuzzing. | 8 bugs on DuckDB v1.4.1: 1 assertion failure, 2 heap buffer overflows, 5 "abnormal errors" (INTERNAL errors; at least #20481 was found under `pragma enable_verification`, and its #20615 example also starts with that pragma). #20615: `LAG(varchar_col, 10, 3) OVER()` - default value of a mismatched type -> wild pointer in string copy. #20481: `LEAD(1) OVER()` + `COALESCE` -> UNKNOWN type reaches physical planning. | Bug classes: window functions with default/argument type mismatches; unresolved types; verification-mode internal errors. Our function fuzzer skips window functions and never used `enable_verification`. |
| **CODDTest**, Zhang & Rigger, SIGMOD 2025 **(read)** | Oracle: replace a sub-expression / sub-query in a predicate by its computed constant; results must not change. | 45 bugs over SQLite, MySQL, CockroachDB, DuckDB, TiDB (24 logic). 2 DuckDB crashes. | Cheap oracle to add for scalar subqueries in predicates. |
| **JoinEquiv** ("Set-theoretic ... inner join optimizations"), arXiv 2606.23294 **(read)** | INNER/NATURAL JOIN = intersection: compare against `LEFT JOIN INTERSECT ALL RIGHT JOIN`, `A EXCEPT (A EXCEPT B)`; NOT NULL columns only. | 3 DuckDB logic bugs: #20483 (NATURAL JOIN vs INTERSECT ALL), #20486 (NATURAL JOIN extra rows; PRIMARY KEY BOOL/DOUBLE columns), #20608 (EXCEPT, string case differs, regression 1.3.0 -> 1.4.3). | Needs tables with PRIMARY KEY / indexes and mismatched join-key types, which our generators lack. |
| **Argus**, Mang et al., SIGMOD 2026, arXiv 2510.06663 **(snippet)** | LLM proposes equivalent-query skeletons; a SQL equivalence solver verifies them; instantiated as tests. Without the solver, false positives dominate. | Reports DuckDB among systems (counts differ between sources). | Related work; confirms LLM-only oracles are unreliable. |
| **ShQveL / LLM-synthesised SQL features**, arXiv 2505.02012 **(snippet)** | LLM writes SQL features that plug into a SQLancer-style generator. | Higher branch coverage than SQLancer++ on DuckDB; direct LLM generation found no bugs there. | Same lesson as Argus. |
| **MIST**, arXiv 2603.21530 (ICSE 2026 industry) **(snippet)** | LLM seeds + Monte-Carlo-tree-search mutation guided by coverage. | Coverage gains on DuckDB. | Matches our coverage-guided loop. |
| **SQLRight** (USENIX Sec 2022), **SQLancer++** (arXiv 2503.21424), **QPG/CERT** (Rigger group) **(snippet)** | Coverage-guided logic-bug fuzzing; adaptive generators; query-plan-guided testing; cardinality-estimation oracle. | Not DuckDB-specific. | Background / baselines. |

## What the evidence says about where bugs are

1. **Sanitizers and DuckDB's own verification mode, not new oracles on the plain build, produced most DuckDB bugs.**
   SmartFuzz's 8 DuckDB bugs are all crash-class or INTERNAL errors (no logic bugs). Our wheel-only NoREC/TLP/differential
   runs on 2.0.0 (about 50k checks) found nothing; DuckDB's CI already runs those families.
2. **Variants of known bugs.** SmartFuzz's best DuckDB crash came from recombining a *historical* crash feature. Upstream
   closes bugs fast, so the productive question is "what is the sibling code path of the fix?".
3. **Upstream is being mined for one class right now:** negation / overflow of INT64_MIN (#26365, #26366, #25072,
   #25084, #25085, PR #26615 - all closed within days). Do not spend time on plain INT64_MIN overflow without
   checking for duplicates first.
4. **All bugs in these papers were found on 1.1-1.4 and are fixed in 2.0.0.** The value is the *class*, not the bug.
5. Window functions, COALESCE/type resolution, NATURAL JOIN with mixed key types, PRIMARY KEY (ART) join paths and
   `EXCEPT`/`INTERSECT ALL` are recurring DuckDB bug locations.

## Plan derived from this (ranked)

1. Run existing generators on the ASan+UBSan 2.0.0 build with `PRAGMA enable_verification` / `debug_verification_mode`
   (`harness/asan_replay.py`, `fuzz_functions.py --emit-jsonl`).
2. Mine closed upstream issues for crash / INTERNAL / sanitizer reproducers and replay + mutate them on 2.0.0
   (crash-reuse without an LLM); hunt siblings of recent fixes.
3. Add window-function and mixed-type templates (LAG/LEAD defaults, COALESCE) and JoinEquiv-style oracles with
   PRIMARY KEY tables to `bottomup.py`.
4. CODDTest-style constant propagation for scalar subqueries.
5. LLM step (doc-guided seeds for 2.0.0 features) once a local model is reachable; until then hand-written templates,
   which the paper must describe honestly as such.

Always: confirm `duckdb.__version__` / source id is the released 2.0.0, replay any crash alone without resource caps,
and check upstream issues *and open PRs* before calling anything new.
