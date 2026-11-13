# Dossier e52d21fcdb - Fix cardinality estimates of empty timestamp ranges and of products of very large inputs

* date 2026-10-13; branch origin/main; PR unknown
* source files changed: src/function/table/range.cpp, src/optimizer/statistics/operator/propagate_join.cpp
* tests added/changed: test/sql/table_function/range_cardinality_extremes.test

## The fix (diff, pre-fix lines start with `-`)

```diff
diff --git a/src/function/table/range.cpp b/src/function/table/range.cpp
index 8af91754c72..038189e31b6 100644
--- a/src/function/table/range.cpp
+++ b/src/function/table/range.cpp
@@ -259,13 +259,14 @@ struct RangeDateTimeBindData : public TableFunctionData {
 		}
 		int64_t delta = 0;
 		if (!TrySubtractOperator::Operation(bounds[1].value, bounds[0].value, delta)) {
 			return;
 		}
 
-		cardinality = idx_t(delta / increment);
+		// a step pointing away from the end produces an empty range
+		cardinality = delta / increment < 0 ? 0 : idx_t(delta / increment);
 	}
 
 	idx_t cardinality;
 };
 
 template <bool GENERATE_SERIES>
diff --git a/src/optimizer/statistics/operator/propagate_join.cpp b/src/optimizer/statistics/operator/propagate_join.cpp
index 8d3eaa6b392..b0c491bd30b 100644
--- a/src/optimizer/statistics/operator/propagate_join.cpp
+++ b/src/optimizer/statistics/operator/propagate_join.cpp
@@ -225,14 +225,18 @@ void StatisticsPropagator::MultiplyCardinalities(unique_ptr<NodeStatistics> &sta
 	if (!stats->has_estimated_cardinality || !new_stats.has_estimated_cardinality || !stats->has_max_cardinality ||
 	    !new_stats.has_max_cardinality) {
 		stats = nullptr;
 		return;
 	}
 	stats->estimated_cardinality = MaxValue<idx_t>(stats->estimated_cardinality, new_stats.estimated_cardinality);
-	auto new_max = Hugeint::Multiply(NumericCast<int64_t>(stats->max_cardinality),
-	                                 NumericCast<int64_t>(new_stats.max_cardinality));
+	hugeint_t new_max;
+	if (!Hugeint::TryMultiply(Hugeint::Convert(stats->max_cardinality), Hugeint::Convert(new_stats.max_cardinality),
+	                          new_max)) {
+		stats = nullptr;
+		return;
+	}
 	if (new_max < NumericLimits<int64_t>::Maximum()) {
 		int64_t result;
 		if (!Hugeint::TryCast<int64_t>(new_max, result)) {
 			throw InternalException("Overflow in cast in statistics propagation");
 		}
 		D_ASSERT(result >= 0);

```

## Reproducer from the fix's own test

```sql
-- test/sql/table_function/range_cardinality_extremes.test
SELECT count(*) FROM range(TIMESTAMP '1992-01-01', TIMESTAMP '1992-12-31', INTERVAL '-1 MONTH') t1, range(3) t2;
SELECT count(*) FROM range(TIMESTAMP '1992-12-31', TIMESTAMP '1992-01-01', INTERVAL '1 MONTH') t1, range(3) t2;
SELECT count(*) FROM generate_series(TIMESTAMP '1992-12-31', TIMESTAMP '1992-01-01', INTERVAL '1 day') t1, range(3) t2;
EXPLAIN SELECT count(*) FROM range(-9223372036854775808, 9223372036854775807) t1, range(2) t2;
EXPLAIN SELECT count(*) FROM range(-9223372036854775808, 9223372036854775807) t1, range(-9223372036854775808, 9223372036854775807) t2;
SELECT count(*) FROM range(TIMESTAMP '1992-01-01', TIMESTAMP '1992-12-31', INTERVAL '1 MONTH') t1, range(3) t2;
```

## Your task (this bug is KNOWN and exists in 2.0.0 - do not report it)
1. State the bug pattern in one sentence (what assumption did the old code make?).
2. `grep -rn` the pattern in `$FUZZ_WORK/duckdb/src` and `extension/`: list other sites with the same assumption.
3. For each site, write a reproducer (or a generator, see docs/AGENT_BRIEF.md) and run it with `verify_finding.py --dev-site $FUZZ_WORK/dev-snapshot`. Fails on 2.0.0 AND on the snapshot = sibling or incomplete fix.
4. Write what you tried and the result in AGENT_NOTES.md, including the negative results.