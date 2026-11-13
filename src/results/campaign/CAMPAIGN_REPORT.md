# DuckDB Directed Fuzzing Campaign Report

**Campaign ID:** `camp_1792681911_hybrid_asan`  
**Status:** `COMPLETED`  
**Elapsed Time:** 43207.6s (720.1m / 720.0m target)  
**Build:** `asan` | **Mode:** `hybrid` | **Random Seed:** `42`  

## 1. Executive Summary & Throughput

| Metric | Total | Rate / Percentage |
| :--- | :--- | :--- |
| **Queries Executed** | 66,110 | 1.5 QPS |
| **Semantically Valid** | 8,286 | 12.5% |
| **Syntax / Catalog Errors** | 57,737 | 87.5% |
| **Raw Anomalies Caught** | 87 | - |
| **Unique Triaged Defects** | 0 | - |

## 2. Generator Performance Breakdown

| Generator | Queries | Throughput (QPS) | Valid % | Crashes |
| :--- | :--- | :--- | :--- | :--- |
| `metamorphic` | 15,390 | 1.1 | 0.0% | 0 |
| `mutation` | 24,620 | 5.4 | 33.7% | 34 |
| `seeds` | 0 | 0.0 | 0.0% | 0 |
| `sqlsmith` | 26,100 | 1.1 | 0.0% | 53 |

## 3. Verified Findings & Upstream Triage (N8 Filtered)

Found 0 unique non-noise defects across all executed engines.

| ID | Kind / Outcome | Config | Disabled Optimizers | Stmts | Stock Build | Pinned-Main (N2) | Upstream Match |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| *None* | - | - | - | - | - | - | - |
