# Dossier 8de20aec8b - fix sequence-operator failure

* date 2026-10-05; branch origin/main; PR unknown
* source files changed: src/execution/operator/helper/physical_verify_vector.cpp
* tests added/changed: test/sql/execution/physical_verify_vector.test

## The fix (diff, pre-fix lines start with `-`)

```diff
diff --git a/src/execution/operator/helper/physical_verify_vector.cpp b/src/execution/operator/helper/physical_verify_vector.cpp
index 7bf7bacb12b..5caaa5b6c3d 100644
--- a/src/execution/operator/helper/physical_verify_vector.cpp
+++ b/src/execution/operator/helper/physical_verify_vector.cpp
@@ -53,12 +53,54 @@ OperatorResultType VerifyEmitDictionaryVectors(const DataChunk &input, DataChunk
 
 struct ConstantOrSequenceInfo {
 	vector<Value> values;
 	bool is_constant = true;
 };
 
+static const vector<Value> &GetNestedValueChildren(const Value &value) {
+	switch (value.type().InternalType()) {
+	case PhysicalType::STRUCT:
+		return StructValue::GetChildren(value);
+	case PhysicalType::LIST:
+		return ListValue::GetChildren(value);
+	case PhysicalType::ARRAY:
+		return ArrayValue::GetChildren(value);
+	default:
+		throw InternalException("Expected a nested value");
+	}
+}
+
+static bool CanShareConstantValue(const Value &left, const Value &right) {
+	if (left.type() != right.type()) {
+		return false;
+	}
+	if (left.IsNull() || right.IsNull()) {
+		return left.IsNull() == right.IsNull();
+	}
+	switch (left.type().InternalType()) {
+	case PhysicalType::STRUCT:
+	case PhysicalType::LIST:
+	case PhysicalType::ARRAY: {
+		// Compare VARIANT storage children directly to preserve payload types and object fields.
+		auto &left_children = GetNestedValueChildren(left);
+		auto &right_children = GetNestedValueChildren(right);
+		if (left_children.size() != right_children.size()) {
+			return false;
+		}
+		for (idx_t i = 0; i < left_children.size(); i++) {
+			if (!CanShareConstantValue(left_children[i], right_children[i])) {
+				return false;
+			}
+		}
+		return true;
+	}
+	default:
+		return ValueOperations::NotDistinctFrom(left, right);
+	}
+}
+
 OperatorResultType VerifyEmitSequenceVector(const DataChunk &input_p, DataChunk &chunk, OperatorState &state_p) {
 	auto &state = state_p.Cast<VerifyVectorState>();
 	D_ASSERT(state.const_idx < input_p.size());
 
 	// FIXME: work-around for variant bug...
 	DataChunk input;
@@ -94,13 +136,13 @@ OperatorResultType VerifyEmitSequenceVector(const DataChunk &input_p, DataChunk
 		info.is_constant = true;
 		for (idx_t k = state.const_idx; k < input.size(); k++) {
 			auto val = input.data[c].GetValue(k);
 			if (info.values.empty()) {
 				info.values.push_back(std::move(val));
 			} else if (info.is_constant) {
-				if (!ValueOperations::DistinctFrom(val, info.values[0]) && can_be_constant) {
+				if (can_be_constant && CanShareConstantValue(val, info.values[0])) {
 					// found the same value! continue
 					info.values.push_back(std::move(val));
 					continue;
 				}
 				// not the same value - can we convert this into a sequence vector?
 				if (!can_be_sequence) {

```

## Reproducer from the fix's own test

```sql
-- test/sql/execution/physical_verify_vector.test
SET debug_verify_vector = 'dictionary_operator';
SELECT i, CASE WHEN i % 2 = 0 THEN NULL ELSE i::VARCHAR END
FROM range(4) t(i);
SET debug_verify_vector = 'constant_operator';
SELECT i, i % 2
FROM range(4) t(i);
SET debug_verify_vector = 'sequence_operator';
SELECT i::TINYINT, (i * 200)::SMALLINT, CASE WHEN i = 2 THEN NULL ELSE i END
FROM range(4) t(i);
SELECT i, i / 2, INTERVAL '1 day' * i
FROM range(4) t(i);
SELECT *
FROM (VALUES (1, 5), (1, 5), (2, 5), (4, 5)) t(irregular, constant_value);
SELECT * FROM (VALUES (1), (2), (4)) t(irregular);
SELECT *
FROM (VALUES
	((-128)::TINYINT, (-32768)::SMALLINT, (-2147483648)::INTEGER, (-9223372036854775808)::BIGINT),
	(127::TINYINT, 32767::SMALLINT, 2147483647::INTEGER, 9223372036854775807::BIGINT)
) t(tiny_value, small_value, integer_value, bigint_value);
SELECT * FROM (VALUES (1), (NULL)) t(nullable_value);
SELECT unnest([empty_struct::VARIANT, struct::VARIANT]) FROM test_all_types();
SELECT variant_typeof(v), variant_typeof(s.v), variant_typeof(l[1]), variant_typeof(a[1])
FROM (
	SELECT v, {'v': v} AS s, [v] AS l, [v]::VARIANT[1] AS a
	FROM (VALUES (42::VARIANT), (42::DOUBLE::VARIANT), ('42'::VARIANT), (NULL::VARIANT)) t(v)
);
SET debug_verify_vector = 'nested_shuffle';
SELECT [i, i + 1], {'id': i, 'label': i::VARCHAR}
FROM range(4) t(i);
SET debug_verify_vector = 'variant_vector';
SELECT i + 1 FROM range(4) t(i);
SET debug_verify_vector = 'shredded_vector';
SELECT i::VARIANT FROM range(4) t(i);
```

## Your task (this bug is KNOWN and exists in 2.0.0 - do not report it)
1. State the bug pattern in one sentence (what assumption did the old code make?).
2. `grep -rn` the pattern in `$FUZZ_WORK/duckdb/src` and `extension/`: list other sites with the same assumption.
3. For each site, write a reproducer (or a generator, see docs/AGENT_BRIEF.md) and run it with `verify_finding.py --dev-site $FUZZ_WORK/dev-snapshot`. Fails on 2.0.0 AND on the snapshot = sibling or incomplete fix.
4. Write what you tried and the result in AGENT_NOTES.md, including the negative results.