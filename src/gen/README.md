# gen/ — schema and data generator  (owner: Jahnvi)

Task **J4** (core). Produce diverse, valid DDL + data so that (a) the LLM
prompt has realistic schemas to refer to and (b) fuzzed queries have
interesting tables to run on.

Cover: nested types (STRUCT/LIST/MAP/ARRAY/UNION), NULL-heavy columns,
tables of 2047/2048/2049/4096 rows (DuckDB's vector size is 2048), extreme
values (INT min/max, DECIMAL edges, empty strings, NaN/Inf), many-column tables.

Contract: `gen/out/*.sql`, one self-contained script per schema (CREATE TABLE
+ INSERT), runnable on a fresh in-memory database.
Entry point: `python gen/gen_schemas.py --n 50 --seed 0 --out gen/out`
