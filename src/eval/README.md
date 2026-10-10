# eval/ — evaluation harness  (owner: Prabhnoor)

Tasks: **P5** harness, **P7** metrics logging. Answers RQ1 (coverage over time)
and RQ2 (valid-query rate).

Arms, same time budget and seeds: (a) mutation baseline, (b) LLM only,
(c) LLM + static hints. Others contribute: Niket adds the OSS-Fuzz/SQLsmith
arms (N4), Jahnvi the hints analysis (J7).

Metrics schema (one JSON object per batch, appended to results/eval-<arm>.jsonl):
`{"arm", "t_sec", "n_queries", "n_parse_ok", "n_bind_ok", "n_exec_ok",
  "lines_hit", "branches_hit", "new_crashes"}`

Raw outputs used in the paper are copied to paper/data/.
