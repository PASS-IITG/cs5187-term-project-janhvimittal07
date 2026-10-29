# Dossier 18e7afc5fe - Fix reading packed zstd segments

* date 2026-10-05; branch origin/main; PR unknown
* source files changed: src/storage/compression/zstd.cpp
* tests added/changed: test/sql/storage/compression/zstd/zstd_packed_segment.test

## The fix (diff, pre-fix lines start with `-`)

```diff
diff --git a/src/storage/compression/zstd.cpp b/src/storage/compression/zstd.cpp
index 31aca81a3eb..9a7819281c1 100644
--- a/src/storage/compression/zstd.cpp
+++ b/src/storage/compression/zstd.cpp
@@ -826,36 +826,36 @@ public:
 			page_end = block->GetBlockSize();
 		}
 
 		reader.Align(sizeof(string_length_t));
 		auto string_lengths = reader.ReadArray<string_length_t>(metadata.count);
 
-		const auto page_remaining = page_end - reader.Position();
-		// Exclude the next page ID so it isn't passed to the decompressor.
-		const auto data_remaining = page_remaining > sizeof(block_id_t) ? page_remaining - sizeof(block_id_t) : 0;
-		auto compressed_size = metadata.compressed_size;
-		if (compressed_size > data_remaining) {
-			if (data_remaining == 0) {
-				ThrowZSTDPageTooSmall();
-			}
-			compressed_size = data_remaining;
-		}
-		auto compressed_data = reader.ReadBytes(compressed_size);
-
 		current_vector = make_uniq<ZSTDVectorScanState>(metadata, string_lengths, reader);
 		auto &scan_state = *current_vector;
 		const auto first_id = metadata.block_id == INVALID_BLOCK ? segment.GetBlockId() : metadata.block_id;
 		scan_state.visited_blocks.insert(first_id);
 		if (data_handle.IsValid()) {
 			scan_state.buffer_handles.push_back(std::move(data_handle));
 		}
 
 		// Update the in_buffer to point to the start of the compressed data frame
+		if (metadata.block_id != INVALID_BLOCK || segment.GetBlockOffset() == 0) {
+			const auto page_remaining = page_end - scan_state.reader.Position();
+			// Exclude the next page ID so it isn't passed to the decompressor.
+			const auto data_remaining = page_remaining > sizeof(block_id_t) ? page_remaining - sizeof(block_id_t) : 0;
+			if (metadata.compressed_size > data_remaining && data_remaining == 0) {
+				ThrowZSTDPageTooSmall();
+			}
+			scan_state.in_buffer.size = MinValue(metadata.compressed_size, data_remaining);
+		} else {
+			// A packed primary segment cannot contain a vector that continues onto another block.
+			scan_state.in_buffer.size = MinValue(metadata.compressed_size, scan_state.reader.Remaining());
+		}
+		auto compressed_data = scan_state.reader.ReadBytes(scan_state.in_buffer.size);
 		scan_state.in_buffer.src = compressed_data.data();
 		scan_state.in_buffer.pos = 0;
-		scan_state.in_buffer.size = compressed_data.size();
 
 		// Initialize the context for streaming decompression
 		duckdb_zstd::ZSTD_DCtx_reset(decompression_context, duckdb_zstd::ZSTD_reset_session_only);
 		duckdb_zstd::ZSTD_DCtx_refDDict(decompression_context, nullptr);
 
 		if (internal_offset) {

```

## Reproducer from the fix's own test

```sql
-- test/sql/storage/compression/zstd/zstd_packed_segment.test
SET threads=1;
CREATE TABLE db.t(p VARCHAR USING COMPRESSION UNCOMPRESSED, s VARCHAR USING COMPRESSION ZSTD);
INSERT INTO db.t SELECT CASE WHEN i=0 THEN repeat('b',809) ELSE repeat('a',7) END, 'string-' || lpad(i::VARCHAR,9,'0') FROM range(1000) t(i);
SELECT compression, block_offset FROM pragma_storage_info('t') WHERE column_name='s' AND segment_type='VARCHAR';
SELECT SUM(strlen(s)) FROM t;
SET debug_force_fetch_row=true;
SELECT SUM(strlen(s)) FROM t;
```

## Your task (this bug is KNOWN and exists in 2.0.0 - do not report it)
1. State the bug pattern in one sentence (what assumption did the old code make?).
2. `grep -rn` the pattern in `$FUZZ_WORK/duckdb/src` and `extension/`: list other sites with the same assumption.
3. For each site, write a reproducer (or a generator, see docs/AGENT_BRIEF.md) and run it with `verify_finding.py --dev-site $FUZZ_WORK/dev-snapshot`. Fails on 2.0.0 AND on the snapshot = sibling or incomplete fix.
4. Write what you tried and the result in AGENT_NOTES.md, including the negative results.