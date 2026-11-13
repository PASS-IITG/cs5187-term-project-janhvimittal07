# Plan: from 16 to 50 DuckDB bugs (written 2026-11-11, execute from 2026-11-12)

Status today: 16 issues filed, 2 fixed upstream, 7 more with fix PRs open. hunt1 (40 Claude calls, 13 targets) gave
4 new issues. Yield is falling on the current family (constants at type limits in optimizer rewrites), so 34 more bugs
need NEW FAMILIES, not just more calls. Every part below adds a new family, a new target source, or less waste.

Rule for every new piece: it needs a positive control (re-finds one of our filed bugs) and a negative control (a
cheat or a known-equivalent rewrite must NOT be reported) before it runs unattended. New oracles always produce
false positives first (see memory: oracle false positives).

## Part 0. Fixes first (about 1 hour)

0.1 Stale nightly. `nightly.py` takes the newest Main run; on 8–9 Nov that run had no CLI artifact, so triage said
    "main nightly: unavailable". Fix: walk back to the newest run that HAS `duckdb-cli-linux-amd64`; if older than 3
    days, fall back to the local main build and say so in CANDIDATES.md.
0.2 Re-check the 9 hunt1 candidates on a current main binary.
0.3 Call budget: `--max-llm-calls` per day, Opus only for round 1, Sonnet for repairs. Log calls per accepted
    generator and per candidate so the paper can report cost per bug.

## Part 1. Rule cards v2: more and better targets

1.1 New target sources (today only `src/optimizer/rule/*` + optimizers, 50 cards):
    - statistics propagation: `src/optimizer/statistics/` and every function's `statistics` callback (for example
      arithmetic, date_part, casts). A wrong min/max lets a filter or join drop rows silently.
    - filter pruning: zone maps / table filters in storage, `filter_pushdown`, `join_filter_pushdown`, dynamic
      filters from top-N, `build_probe_side`.
    - precision casts: decimal scale/width casts, `TryCast` paths, cast-to-smaller-type folding.
    - late materialization, compressed materialization, common subplan, expression reuse.
    Each source needs a reach signal: a marker, an `EXPLAIN` difference, or (new) coverage of the target lines (Part 2).
1.2 Fix-dossier neighbours (what found 28541 next to 28535): for every fix commit, list what the fix did NOT touch:
    - type siblings: TIMESTAMP -> _S/_MS/_NS/TZ, DATE; HUGEINT -> UHUGEINT; INTEGER -> UBIGINT; DOUBLE -> FLOAT/DECIMAL;
      VARCHAR -> LIST/STRUCT/ARRAY/VARIANT;
    - code siblings: other functions in the same file with the same pattern (same helper called, same early exit);
    - put these on the card as "the fix covered X; check Y, Z".
1.3 Bug-family transfer from our own 16 bugs. Families: signed zero (0.0 vs -0.0: 28559, 28566), NaN (28052),
    range edge (28531, 28532, 28534, 28542), constant wrap (28535, 28541), wrong type assumption (28562, 28565),
    regex edge (28536). Card = (family x rule not yet tried with it), ranked. The family's minimal pattern goes into
    the prompt as a worked example.
1.4 Upstream mining: closed "wrong result" issues of the last 6 months -> fixing file -> boost that file's cards
    (bugs cluster).
1.5 Yield-based ranking (from the slide's feedback loop): replace the static score by a bandit: score = prior +
    candidates found / calls spent, decayed per round. Targets that produce nothing after 2 rounds retire faster;
    productive ones get another round with a different family.

## Part 2. Use coverage (we have `build-cov`, a gcov build at v2.0.0-dev88569)

2.1 Check first: version gap dev88569 vs 2.0.0 for the target files (`git diff --stat`). If the target files differ,
    rebuild `build-cov` at v2.0.0 (about 1 h, -j4, run when the laptop is idle).
2.2 Coverage gate (the slide's "diff coverage" idea, applied to the target): run an accepted generator's cases on
    `build-cov`, measure line and branch coverage of the TARGET file only (fast: reset counters, run, gcov one file).
    Gate: the generator must execute the rewrite's body, not only its early exits. This is stronger than marker reach
    for rules without markers (operators like `+`, LIKE).
2.3 Coverage feedback in repair rounds: tell the LLM exactly which early exits and branches never ran
    ("line 142 `if (constant.IsNull())` was never true"). This turns repair rounds from "try again" into directed steps.
2.4 Card ranking: lines of the target that our corpus and DuckDB's own tests never execute rank higher.
2.5 Paper numbers: target-file coverage, baseline (sqlsmith + classic sweeps) vs Method C vs Method D.
Cost note: one worker, coverage runs between LLM calls, never in parallel with a build (8 GB RAM).

## Part 3. Ideas from the other team's slide

3.1 More oracles (new families, the biggest lever for 50):
    - TLP: Q vs (Q WHERE p) UNION ALL (Q WHERE NOT p) UNION ALL (Q WHERE p IS NULL); also for aggregates and DISTINCT.
    - NoREC: WHERE p vs SUM(CASE WHEN p THEN 1 ELSE 0 END): the optimizer cannot rewrite the second form.
    - scalar-subquery wrap: e -> (SELECT e); blocks constant folding and pushdown.
    - cross-version: 2.0.0 vs current main on the same case. Also gives regressions and "already fixed" for free.
    - execution variants beyond disabled_optimizers: threads=1 vs 8, verify_parallelism, persistent DB + CHECKPOINT
      vs in-memory, force_compression, prepared statement vs literal, preserve_insertion_order.
    Each oracle: its own normalisation (unordered results, float noise), its own positive/negative controls.
3.2 Tree-based reduction: parse with DuckDB's own `json_serialize_sql`, shrink the expression tree, serialize back
    with `json_deserialize_sql`. Every step stays valid SQL, so minimisation is faster and gives cleaner reproducers
    than text deletion.
3.3 Triage localisation: (a) which single optimizer or setting causes the difference (bisect over the list);
    (b) version bisect over release wheels 1.2 -> 1.5 -> 2.0 -> main: "regression since 1.4" helps maintainers and
    the paper; (c) reproduce x3 to drop flaky hits.
3.4 Corpus feedback: keep cases that gave a new plan shape or new target coverage and give them back to the LLM as
    seeds in later rounds.

## Part 4. Execution order (tomorrow and after)

Day 1: Part 0; oracles TLP + NoREC + subquery wrap with controls (3.1); triage localisation (3.3 a, c).
Day 2: cards v2 (1.1 to 1.3), coverage check/rebuild (2.1), coverage gate + feedback (2.2, 2.3). Run controls.
Day 3+: `hunt2` unattended, rotating: new sources x families x oracles. Read candidates every morning; the user files.
Later: cross-version oracle, tree reduction, bandit ranking, execution variants.

## Part 5. Honest expectations and risks

- 34 more bugs at hunt1's rate (about 1 per 10 calls) is about 340 calls, several days of Pro quota. The new oracles
  and sources are what can lift the rate; the old family alone will not reach 50.
- Duplicates: more oracles -> more rediscoveries; the registry and upstream search must run before anything is shown.
- Maintainer goodwill: many similar issues at once can look like spam. Group siblings into one issue where they share
  a root cause, keep reproducers minimal, and file at a steady pace. DuckDB's AI policy: issue and PR text must be
  explainable by the person filing.
- Each count in the paper must come from a real run (ledger, RUN_LOG, CANDIDATES), not from this plan.
