# Dossier c2dbda790e - Fix CI failures: dict_count bound, union_casts include, empty Parquet dictionary assert, levenshtein test

* date 2026-10-21; branch origin/main; PR unknown
* source files changed: extension/parquet/writer/primitive_column_writer.cpp, src/function/cast/union_casts.cpp, src/storage/compression/dict_fsst/decompression.cpp
* tests added/changed: test/sql/function/string/test_levenshtein_interrupt.test

## The fix (diff, pre-fix lines start with `-`)

```diff
diff --git a/extension/parquet/writer/primitive_column_writer.cpp b/extension/parquet/writer/primitive_column_writer.cpp
index 085de462b68..72d792c14f7 100644
--- a/extension/parquet/writer/primitive_column_writer.cpp
+++ b/extension/parquet/writer/primitive_column_writer.cpp
@@ -450,13 +450,12 @@ void PrimitiveColumnWriter::PrepareWrite(ColumnWriterState &state_p) {
 	FlushPage(state);
 	if (HasDictionary(state)) {
 		FlushDictionary(state, state.stats_state.get());
 	}
 
 	for (auto &write_info : state.write_info) {
-		D_ASSERT(write_info.page_header.uncompressed_page_size > 0);
 		D_ASSERT(!write_info.prepared_header);
 		D_ASSERT(!write_info.prepared_payload);
 
 		write_info.prepared_header = writer.PrepareWrite(write_info.page_header);
 		auto payload_buffer = make_uniq<ParquetPagePayloadBuffer>(
 		    write_info.compressed_size, std::move(write_info.temp_writer), std::move(write_info.compressed_buf));
diff --git a/src/function/cast/union_casts.cpp b/src/function/cast/union_casts.cpp
index d6e3c3e91cb..2b5c3867b31 100644
--- a/src/function/cast/union_casts.cpp
+++ b/src/function/cast/union_casts.cpp
@@ -1,7 +1,8 @@
 #include "duckdb/common/vector_operations/vector_operations.hpp"
+#include "duckdb/common/operator/cast_operators.hpp"
 #include "duckdb/common/vector/constant_vector.hpp"
 #include "duckdb/common/vector/flat_vector.hpp"
 #include "duckdb/common/vector/union_vector.hpp"
 #include "duckdb/common/exception/conversion_exception.hpp"
 #include "duckdb/function/cast/bound_cast_data.hpp"
 #include "duckdb/function/cast/cast_function_set.hpp"
diff --git a/src/storage/compression/dict_fsst/decompression.cpp b/src/storage/compression/dict_fsst/decompression.cpp
index d36f25e8678..b93704a000a 100644
--- a/src/storage/compression/dict_fsst/decompression.cpp
+++ b/src/storage/compression/dict_fsst/decompression.cpp
@@ -104,13 +104,13 @@ void CompressedStringScanState::Initialize(bool initialize_dictionary) {
 	}
 
 	// Compute the aligned count in a 64-bit type: dict_count is read directly from the file and can be
 	// close to UINT32_MAX, which would overflow the uint32_t arithmetic AlignValue<uint32_t> performs.
 	auto aligned_dict_count =
 	    AlignValue<idx_t, BitpackingPrimitives::BITPACKING_ALGORITHM_GROUP_SIZE>(static_cast<idx_t>(dict_count));
-	if (aligned_dict_count * sizeof(uint32_t) > segment.GetBlockSize()) {
+	if (static_cast<idx_t>(dict_count) > segment.count.load()) {
 		throw DataCorruptionException(
 		    "Failed to scan dictionary string - dict_count was out of range. Database file appears to be corrupted.");
 	}
 	string_lengths.resize(aligned_dict_count);
 	BitpackingPrimitives::UnPackBuffer<uint32_t>(data_ptr_cast(string_lengths.data()),
 	                                             data_ptr_cast(string_lengths_ptr), dict_count, string_lengths_width);

```

## Reproducer from the fix's own test

```sql
-- test/sql/function/string/test_levenshtein_interrupt.test
SET max_execution_time=1000;
SELECT levenshtein(repeat('a',300000), repeat('b',300000));
SET max_execution_time=0;
SELECT levenshtein('kitten', 'sitting'), damerau_levenshtein('ab', 'ba');
```

## Your task (this bug is KNOWN and exists in 2.0.0 - do not report it)
1. State the bug pattern in one sentence (what assumption did the old code make?).
2. `grep -rn` the pattern in `$FUZZ_WORK/duckdb/src` and `extension/`: list other sites with the same assumption.
3. For each site, write a reproducer (or a generator, see docs/AGENT_BRIEF.md) and run it with `verify_finding.py --dev-site $FUZZ_WORK/dev-snapshot`. Fails on 2.0.0 AND on the snapshot = sibling or incomplete fix.
4. Write what you tried and the result in AGENT_NOTES.md, including the negative results.