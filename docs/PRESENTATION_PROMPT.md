PROMPT FOR ANOTHER AI (paste this, and attach our old slides if you have them)

Update our existing slide deck (or make a new one, about 12 to 15 slides) for our CS5187 term project
"Semantic LLM-Directed Fuzzing of DuckDB" (team: Prabhnoor Singh, Niket Garg, Janhvi Mittal, IIT Guwahati).
Keep text short and plain on every slide. Use diagrams for the method. Mark our novelties clearly with a "Novel" tag.

SLIDES WANTED
1. Title
2. Problem: wrong-answer bugs in SQL optimizers are silent (no crash). Random fuzzers rarely reach deep optimizer rules.
3. Method overview diagram (pipeline):
   DuckDB source + git fix history -> "rule cards" -> LLM (Claude) writes a test generator -> engine judge
   -> differential oracle -> triage -> human reads candidate -> GitHub issue (+ fix PR)
4. Step 1, rule cards (NOVEL): for each optimizer rule we extract automatically from the code and git history:
   recent fix commits and their tests ("the next bug sits next to the last fix"), every early exit / condition
   under which the rewrite fires, every place it builds or converts a value, its marker functions, and its off switch.
   Targets are ranked by a score (fixes, commits, value-building sites).
5. Step 2, hypothesis-first LLM generation (NOVEL): the LLM reads the real source (read-only tools, empty working
   folder) and must state a bug hypothesis and preconditions before writing a Python generator. It never writes single
   queries; it writes a program that generates hundreds of queries.
6. Step 3, the engine as judge (NOVEL): we do not trust the LLM. Each generator is run on DuckDB and accepted only if
   the rule ACTUALLY fires: a marker call disappears from the plan (rule-level reach), plus plan-shape diversity.
   A cheating generator (names the rule but never triggers it) is rejected; this is a built-in control.
7. Step 4, oracle: same query with the optimizer rule ON vs OFF (SET disabled_optimizers=...) across 5 configurations.
   Different result = candidate bug. Inputs use values from the table rows and 275 type-limit literals over 30 types
   (max/min values, -0.0, infinity, extreme dates) placed in WHERE, CASE, FILTER, JOIN ON, subqueries.
8. Step 5, automatic triage: re-verify, match against a registry of known issues, delta-minimize, test on newest main
   build, search GitHub for duplicates. Runs unattended for hours (ledger resume, crash supervisor, RAM guard,
   keep-awake). A human reads every candidate before anything is filed.
9. Novelties summary: (a) targets mined from code + fix history, (b) LLM must state a hypothesis, (c) LLM writes
   generators not queries, (d) execution-verified rule reach as the judge, (e) positive and negative controls that
   rediscover our own earlier bugs, proving the pipeline still works after every change.
10. Results: 16 issues filed on github.com/duckdb/duckdb, mostly silent wrong results in DuckDB 2.0.0.
    Several confirmed / fixed by maintainers, 4 fix PRs (one of them ours, #28547).
11-12. Top 5 bugs (one per half slide: what, minimal query, wrong vs right answer, link):
   - #28566 unused_columns merges both sides of a LIST join key: [0.0] vs [-0.0] returns the wrong side.
   - #28559 Top-N window elimination returns the other side's join key when keys are 0.0 and -0.0.
   - #28541 uhugeint_col + c1 = c2 silently wrong when a constant is above HUGEINT max (value wraps). Fix PR #28549.
   - #28562 contains(list_column, '') returns true for lists without an empty string (rule folds it to TRUE).
   - #28531 timestamp/date comparison with an out-of-range constant gives wrong rows; our own fix PR #28547.
13. All bugs table (number, short title, status). Open each link to copy the exact title and current status:
   https://github.com/duckdb/duckdb/issues/27759  (INTERNAL error, trigger / delim_join_as_cte)
   https://github.com/duckdb/duckdb/issues/27850  (fixed by #28144)
   https://github.com/duckdb/duckdb/issues/27904  (filed by Niket)
   https://github.com/duckdb/duckdb/issues/28052  (fixed by #28071)
   https://github.com/duckdb/duckdb/issues/28527  (date_trunc rewrite; PR #28528)
   https://github.com/duckdb/duckdb/issues/28531  (timestamp comparison range; our PR #28547)
   https://github.com/duckdb/duckdb/issues/28532  (cast comparison; PR #28539 partial)
   https://github.com/duckdb/duckdb/issues/28534  (date_trunc bound; our fix ready)
   https://github.com/duckdb/duckdb/issues/28535  (arithmetic constants; PR #28540)
   https://github.com/duckdb/duckdb/issues/28536  (PR #28538)
   https://github.com/duckdb/duckdb/issues/28541  (UHUGEINT; PR #28549)
   https://github.com/duckdb/duckdb/issues/28542  (TRY_CAST date vs timestamp returns false not NULL)
   https://github.com/duckdb/duckdb/issues/28559  (top-N window elimination, signed zero)
   https://github.com/duckdb/duckdb/issues/28562  (contains(list, ''))
   https://github.com/duckdb/duckdb/issues/28565  (CASE guard ignored, STRUCT cast pushed into scan)
   https://github.com/duckdb/duckdb/issues/28566  (unused_columns, LIST join key [0.0] vs [-0.0])
14. Honest limits: finds are mostly one family (constants at type limits in optimizer rewrites); returns per target
   shrink, so rotating targets beats longer runs; operator rules (+, LIKE) have no marker function, so their reach is
   measured more weakly. Next: more target sources, other engines (PostgreSQL), aim for ~30 bugs for a paper.
15. Thank you / questions.
