# Dossier 76dfeea86e - fix: external hash join dropping rows through a partial bloom filter

* date 2026-09-18; branch origin/main; PR unknown
* source files changed: src/execution/join_hashtable.cpp, src/execution/operator/join/physical_hash_join.cpp, src/include/duckdb/execution/join_hashtable.hpp, src/include/duckdb/planner/filter/bloom_filter.hpp, src/include/duckdb/planner/filter/table_filter_functions.hpp, src/planner/filter/table_filter_bloom_function.cpp, src/planner/filter/table_filter_functions.cpp, src/planner/table_filter_state.cpp
* tests added/changed: test/sql/join/external/external_join_runtime_filter.test

## The fix (diff, pre-fix lines start with `-`)

```diff
diff --git a/src/execution/join_hashtable.cpp b/src/execution/join_hashtable.cpp
index bd90a79828c..0e97ca63e90 100644
--- a/src/execution/join_hashtable.cpp
+++ b/src/execution/join_hashtable.cpp
@@ -171,15 +171,15 @@ void JoinHashTable::Merge(JoinHashTable &other) {
 		if (info.uncorrelated_condition_rows && other.mark_join_info.uncorrelated_condition_rows) {
 			info.uncorrelated_has_null = info.uncorrelated_has_null || other.mark_join_info.uncorrelated_has_null;
 			info.uncorrelated_condition_rows->Combine(*other.mark_join_info.uncorrelated_condition_rows);
 		}
 	}
 
-	if (bloom_filter.IsInitialized() && other.bloom_filter.IsInitialized()) {
-		bloom_filter.Merge(other.bloom_filter);
-	}
+	// every thread-local table shares this operator's filter - a different one here would mean its rows
+	// never reached the filter that gets published
+	D_ASSERT(!other.bloom_filter || bloom_filter.get() == other.bloom_filter.get());
 
 	sink_collection->Combine(*other.sink_collection);
 
 	// dict_info is the descriptor shared by every per-thread JHT of this join; there is nothing to reconcile
 }
 
@@ -712,14 +712,14 @@ void JoinHashTable::Build(PartitionedTupleDataAppendState &append_state, DataChu
 		return;
 	}
 
 	// hash the keys and obtain an entry in the list
 	// note that we only hash the keys used in the equality comparison
 	Hash(keys, *current_sel, added_count, hash_values);
-	if (bloom_filter.IsInitialized()) {
-		bloom_filter.InsertHashes(hash_values);
+	if (bloom_filter) {
+		bloom_filter->InsertHashes(hash_values, *current_sel, added_count);
 	}
 
 	// Re-reference and ToUnifiedFormat the hash column after computing it
 	source_chunk.data[col_offset].Reference(hash_values);
 	hash_values.ToUnifiedFormat(append_state.chunk_state.vector_data.back().unified);
 
@@ -1000,16 +1000,12 @@ static void InsertHashesLoop(unsafe_optional_ptr<atomic<ht_entry_t>> entries, Ve
 		remaining_count = key_no_match_count;
 	}
 }
 
 void JoinHashTable::InsertHashes(Vector &hashes_v, TupleDataChunkState &chunk_state, InsertState &insert_state,
                                  bool parallel) {
-	// Insert Hashes into the BF
-	if (bloom_filter.IsInitialized()) {
-		bloom_filter.InsertHashes(hashes_v);
-	}
 	auto atomic_entries = GetAtomicEntries();
 	auto &row_locations = chunk_state.row_locations;
 	if (parallel) {
 		InsertHashesLoop<true>(atomic_entries, row_locations, hashes_v, insert_state, *data_collection, *this);
 	} else {
 		InsertHashesLoop<false>(atomic_entries, row_locations, hashes_v, insert_state, *data_collection, *this);
@@ -1069,17 +1065,12 @@ void JoinHashTable::AllocatePointerTable() {
 
 	constexpr uint64_t MAX_HASHTABLE_CAPACITY = (1ULL << 48) - 1;
 	if (capacity >= MAX_HASHTABLE_CAPACITY) {
 		throw InternalException("Hashtable capacity exceeds 48-bit limit (2^48 - 1)");
 	}
 
-	if (should_build_bloom_filter && !bloom_filter.IsInitialized()) {
-		bloom_filter_init_count = MaxValue<idx_t>(Count(), 1);
-		bloom_filter.Initialize(context, bloom_filter_init_count);
-	}
-
 	if (hash_map.get()) {
 		// There is already a hash map
 		auto current_capacity = hash_map.GetSize() / sizeof(ht_entry_t);
 		if (capacity > current_capacity) {
 			// Need more space
 			hash_map = buffer_manager.GetBufferAllocator().TryAllocateHuge(capacity * sizeof(ht_entry_t));
@@ -1097,34 +1088,12 @@ void JoinHashTable::AllocatePointerTable() {
 
 	DUCKDB_LOG(context, PhysicalOperatorLogType, op, "JoinHashTable", "Build",
 	           {{"rows", to_string(data_collection->Count())},
 	            {"size", to_string(data_collection->SizeInBytes() + hash_map.GetSize())}});
 }
 
-void JoinHashTable::PrepareBloomFilterForFinalize() {
-	if (!should_build_bloom_filter) {
-		return;
-	}
-
-	// Finalize scans every build tuple and inserts its hash into the bloom filter.
-	// Make sure any existing filter has enough sectors for the actual build count.
-	const auto build_count = Count();
-	const auto actual_init_count = MaxValue<idx_t>(build_count, 1);
-	if (bloom_filter.IsInitialized()) {
-		const auto current_sectors = BloomFilter::GetNumberOfSectors(bloom_filter_init_count);
-		const auto required_sectors = BloomFilter::GetNumberOfSectors(actual_init_count);
-		if (current_sectors >= required_sectors) {
-			return;
-		}
-	}
-
-	bloom_filter.Reset();
-	bloom_filter_init_count = actual_init_count;
-	bloom_filter.Initialize(context, bloom_filter_init_count);
-}
-
 void JoinHashTable::InitializePointerTable(idx_t entry_idx_from, idx_t entry_idx_to) {
 	// initialize HT with all-zero entries
 	auto entries = GetEntries();
 	std::fill_n(entries.get() + entry_idx_from, entry_idx_to - entry_idx_from, ht_entry_t());
 }
 
@@ -2596,15 +2565,13 @@ void JoinHashTable::ResetForNewIterationSinglePartition() {
 	bitmask = DConstants::INVALID_INDEX;
 	finalized = false;
 	has_null = false;
 	chains_longer_than_one.store(false, std::memory_order_relaxed);
 	total_probe_matches = 0;
 	load_factor = DEFAULT_LOAD_FACTOR;
-	should_build_bloom_filter = false;
-	bloom_filter.Reset();
-	bloom_filter_init_count = 0;
+	bloom_filter.reset();
 	prefix_range_filter.reset();
 	should_build_prefix_range_filter = false;
 	ResetMarkJoinInfo(*this);
 	// The next iteration may rebuild a different small build side, so this iteration's dictionary
 	// state is stale. Keep in lock-step with BuildDictionaryArrays, which sets these four fields.
 	dict_arrays.clear();
diff --git a/src/execution/operator/join/physical_hash_join.cpp b/src/execution/operator/join/physical_hash_join.cpp
index d0db4070250..ee18247bec1 100644
--- a/src/execution/operator/join/physical_hash_join.cpp
+++ b/src/execution/operator/join/physical_hash_join.cpp
@@ -347,12 +347,31 @@ struct LayoutGate {
 		dict_info.reset();
 	}
 };
 
 static idx_t GetBuildSpaceRequirement(ClientContext &context, const JoinHashTable &ht, idx_t num_threads);
 
+//! A bloom filter is only useful if we expect to probe more rows than we build, and it can only be pushed into a
+//! scan for a single equality condition. Both are decided up front, because the filter is filled during Sink.
+static bool ShouldBuildBloomFilter(const PhysicalHashJoin &op) {
+	if (!op.filter_pushdown || op.filter_pushdown->probe_info.empty()) {
+		return false;
+	}
+	idx_t equality_count = 0;
+	for (auto &cond : op.conditions) {
+		const auto cmp = cond.GetComparisonType();
+		if (cmp == ExpressionType::COMPARE_EQUAL || cmp == ExpressionType::COMPARE_NOT_DISTINCT_FROM) {
+			equality_count++;
+		}
+	}
+	if (equality_count != 1) {
+		return false;
+	}
+	return op.children[1].get().estimated_cardinality <= op.children[0].get().estimated_cardinality;
+}
+
 class HashJoinGlobalSinkState : public GlobalSinkState {
 public:
 	HashJoinGlobalSinkState(const PhysicalHashJoin &op_p, ClientContext &context_p)
 	    : context(context_p), op(op_p), num_threads(TaskScheduler::GetScheduler(context).NumberOfThreads()),
 	      temporary_memory_state(TemporaryMemoryManager::Get(context).Register(context)),
 	      initial_radix_bits(num_threads < 100 ? 4 : 5), finalized(false), active_local_states(0), total_size(0),
@@ -381,12 +400,24 @@ public:
 			if (op.filter_pushdown->probe_info.empty() && use_perfect_hash) {
 				// Only computing min/max to check for perfect HJ, but we already can
 				skip_filter_pushdown = true;
 			}
 			global_filter_state = op.filter_pushdown->GetGlobalState(context, op);
 		}
+		InitializeBloomFilter();
+	}
+
+	//! Creates the bloom filter that every thread-local hash table inserts into during Sink
+	void InitializeBloomFilter() {
+		if (skip_filter_pushdown || !ShouldBuildBloomFilter(op)) {
+			hash_table->SetBloomFilter(nullptr);
+			return;
+		}
+		auto filter = make_shared_ptr<BloomFilter>();
+		filter->Initialize(context, MaxValue<idx_t>(op.children[1].get().estimated_cardinality, 1));
+		hash_table->SetBloomFilter(std::move(filter));
 	}
 
 	~HashJoinGlobalSinkState() override {
 		DUCKDB_LOG(context, PhysicalOperatorLogType, op, "PhysicalHashJoin", "GetData",
 		           {{"total_probe_matches", to_string(hash_table->total_probe_matches)}});
 	}
@@ -428,12 +459,13 @@ public:
 		if (op.filter_pushdown) {
 			if (op.filter_pushdown->probe_info.empty() && use_perfect_hash) {
 				skip_filter_pushdown = true;
 			}
 			global_filter_state = op.filter_pushdown->GetGlobalState(context, op);
 		}
+		InitializeBloomFilter();
 		// The next iteration's first chunk publishes the layout afresh: the rerun build may deliver a different
 		// dictionary (or none), so neither a narrowed slot nor the dictionary it indexes into can be carried over.
 		// ResetForNewIterationSinglePartition returned the global JHT to the unpublished state accordingly.
 		layout_gate.Reset();
 		GlobalSinkState::Reset(context);
 	}
@@ -511,12 +543,14 @@ public:
 
 		if (!op.payload_columns.col_types.empty()) {
 			payload_chunk.InitializeEmpty(op.payload_columns.col_types);
 		}
 
 		hash_table = op.InitializeHashTable(context, gstate.hash_table->GetRadixBits());
+		// every thread insert
```

## Reproducer from the fix's own test

```sql
-- test/sql/join/external/external_join_runtime_filter.test
PRAGMA threads=1;
CREATE TABLE b AS SELECT range % 2000 AS k FROM range(1000000);
CREATE TABLE p AS SELECT range % 4000 AS k FROM range(2000000);
SELECT count(*) FROM p WHERE k IN (SELECT k FROM b);
PRAGMA debug_force_external=true;
SELECT count(*) FROM p WHERE k IN (SELECT k FROM b);
PRAGMA debug_force_external=false;
```

## Your task (this bug is KNOWN and exists in 2.0.0 - do not report it)
1. State the bug pattern in one sentence (what assumption did the old code make?).
2. `grep -rn` the pattern in `$FUZZ_WORK/duckdb/src` and `extension/`: list other sites with the same assumption.
3. For each site, write a reproducer (or a generator, see docs/AGENT_BRIEF.md) and run it with `verify_finding.py --dev-site $FUZZ_WORK/dev-snapshot`. Fails on 2.0.0 AND on the snapshot = sibling or incomplete fix.
4. Write what you tried and the result in AGENT_NOTES.md, including the negative results.