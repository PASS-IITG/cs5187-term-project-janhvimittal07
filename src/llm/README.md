# llm/ — LLM query generation  (owner: Prabhnoor)

Tasks: **P1** client, **P2** generator (core), **P3** feedback loop, **P4** prompt experiments.

Flow: pick an uncovered region (coverage/cov.py) + optional hint
(static/hints.json) + a schema (gen/out/*.sql) -> build prompt -> local model
-> batch of SQL -> EXPLAIN-validate (one repair retry) -> save to
`$FUZZ_WORK/corpus/llm/<run>/*.sql`. fuzz.py then sweeps/triages the files.

The model runs on another machine: configure with environment variables
`LLM_BASE_URL` (OpenAI-compatible, e.g. http://host:11434/v1), `LLM_MODEL`,
`LLM_API_KEY` (optional). No paid API is required.
