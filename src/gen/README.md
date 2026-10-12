# DuckDB Schema and Data Generator (Task J4)

**Owner:** Jahnvi (`janhvimittal07`)  
**Specification Reference:** [`docs/WORKPLAN.md` §5 (Task J4) & §9 (Interfaces)](../docs/WORKPLAN.md)

---

## 1. Overview & Purpose

Task J4 implements the schema and test dataset generator for the CS-5187 term project (*Directed LLM-Guided Fuzzing of DuckDB*).
It produces diverse, realistic database schemas with populated tables to serve as:
1. **Prompt Context for LLM Query Generation (Prabhnoor)**: Providing the LLM with table DDLs, complex types, and valid column names to synthesize semantically valid queries.
2. **Setup State for Fuzzing Runs & Oracles (Niket)**: Populating database states where vector boundary bugs, nested type crashes, and null mask discrepancies can be exercised.

---

## 2. Generated Schema Archetypes

The generator produces 5 standalone SQL scripts in `src/gen/out/*.sql`:

| Script | Purpose & Contents | Tables Created |
|---|---|---|
| `primitives_extreme.sql` | All DuckDB primitive scalar types (`TINYINT`..`HUGEINT`, unsigned variants, `FLOAT`, `DOUBLE` with `NaN`/`inf`, `DECIMAL(4,1)`..`DECIMAL(38,10)`, `VARCHAR`, `BLOB`, `DATE`, `TIME`, `TIMESTAMP`, `INTERVAL`, `UUID`) populated with numeric bounds, zeros, and extremes. | `tbl_integers`, `tbl_floats_decimals`, `tbl_strings_temporal` |
| `nested_structures.sql` | DuckDB's complex hierarchical types: `STRUCT`, deeply nested structs, single & multidimensional `LIST`s, list of structs, `MAP(VARCHAR, INT)`, nested maps, and `UNION`s with mixed empty/null elements. | `tbl_structs`, `tbl_lists`, `tbl_maps`, `tbl_unions` |
| `vector_boundaries.sql` | Tables explicitly populated at DuckDB's internal `STANDARD_VECTOR_SIZE` (**2048**) boundaries: **2047** (underflow chunk), **2048** (exact full chunk), **2049** (single-row spillover into chunk #2), and **4096** (2 full chunks). | `tbl_vector_2047`, `tbl_vector_2048`, `tbl_vector_2049`, `tbl_vector_4096` |
| `null_patterns.sql` | Tests DuckDB's validity mask bit patterns: all-NULL (zeroed bitmask), no-NULL (unallocated bitmask), alternating parity NULLs (0101 pattern across 2048 rows), boundary index NULLs (NULL only at index 0, 2047, 2048), and nested NULLs. | `tbl_null_all`, `tbl_null_none`, `tbl_null_alternating`, `tbl_null_boundary_indices`, `tbl_null_nested` |
| `relational_schemas.sql` | Realistic 5-table relational schema with primary keys, foreign keys, numeric quantities, and prices to facilitate complex joins, aggregations, window functions, and CTE query generation. | `customers`, `categories`, `products`, `orders`, `order_items` |

---

## 3. Usage & CLI Options

To generate all SQL files:

```bash
# Generate schemas into default directory (src/gen/out/)
python src/gen/generate_schema.py

# Generate and validate syntax against DuckDB ASan binary
python src/gen/generate_schema.py --validate

# Custom output directory
python src/gen/generate_schema.py --out-dir /path/to/custom/out
```

---

## 4. Validation

Every generated script executes in memory in <0.1s without syntax errors, and creates a combined total of **21 tables** ready for immediate query fuzzing.
All scripts are verified using both `build-asan/duckdb` and the Python `duckdb` module.
