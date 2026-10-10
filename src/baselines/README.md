# baselines/ — OSS-Fuzz and SQLsmith  (owner: Niket)

Task **N4**. The proposal promises a comparison against OSS-Fuzz. Find what
DuckDB's OSS-Fuzz targets are (google/oss-fuzz, projects/duckdb), build and
run them, and record coverage over time with coverage/cov.py using the SAME
time budget and machine as the other arms. Do the same for SQLsmith.

Write results as JSON lines in the schema from eval/README.md so plots can
compare all arms. Scripts: `run_ossfuzz.sh`, `run_sqlsmith.sh` (stubs).
