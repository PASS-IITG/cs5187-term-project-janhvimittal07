# Dossier ad5ad0f7ab - Fix join-order reconstruction of zero-column filters

* date 2026-10-20; branch origin/main; PR unknown
* source files changed: src/include/duckdb/optimizer/join_order/relation_manager.hpp, src/optimizer/join_order/query_graph_manager.cpp, src/optimizer/join_order/relation_manager.cpp
* tests added/changed: test/optimizer/joins/zero_column_filter_reconstruction.test

## The fix (diff, pre-fix lines start with `-`)

```diff
diff --git a/src/include/duckdb/optimizer/join_order/relation_manager.hpp b/src/include/duckdb/optimizer/join_order/relation_manager.hpp
index 1aa886b9d43..f133fc62ce9 100644
--- a/src/include/duckdb/optimizer/join_order/relation_manager.hpp
+++ b/src/include/duckdb/optimizer/join_order/relation_manager.hpp
@@ -57,12 +57,14 @@ public:
 	//! both sides of the join filter, along with the tables & indexes.
 	JoinOrderExtraction ExtractEdges(LogicalOperator &op, vector<reference<LogicalOperator>> &filter_operators,
 	                                 JoinRelationSetManager &set_manager);
 
 	//! Extract the set of relations referred to inside an expression
 	bool ExtractBindings(const Expression &expression, unordered_set<RelationIndex> &bindings);
+	//! Like ExtractBindings, but returns whether the filter can be moved off its operator
+	bool ExtractFilterBindings(const Expression &expression, unordered_set<RelationIndex> &bindings);
 	bool TryNormalizeBinding(ColumnBinding binding, ColumnBinding &normalized) const;
 	bool HasCompleteStats() const;
 	bool AddRelation(LogicalOperator &op, optional_ptr<LogicalOperator> parent, const RelationStats &stats);
 	//! Add an unnest relation which can come from a logical unnest or a logical get which has an unnest function
 	bool AddRelationWithChildren(JoinOrderOptimizer &optimizer, LogicalOperator &op, LogicalOperator &input_op,
 	                             optional_ptr<LogicalOperator> parent, RelationStats &child_stats,
diff --git a/src/optimizer/join_order/query_graph_manager.cpp b/src/optimizer/join_order/query_graph_manager.cpp
index 7f91cf7005a..0bb02e8d1e7 100644
--- a/src/optimizer/join_order/query_graph_manager.cpp
+++ b/src/optimizer/join_order/query_graph_manager.cpp
@@ -483,14 +483,13 @@ void QueryGraphManager::ClearExtractedExpressions() {
 			op.Cast<LogicalComparisonJoin>().conditions.clear();
 			continue;
 		}
 		vector<unique_ptr<Expression>> remaining_expressions;
 		for (auto &expression : op.expressions) {
 			unordered_set<RelationIndex> bindings;
-			relation_manager.ExtractBindings(*expression, bindings);
-			if (bindings.empty()) {
+			if (!relation_manager.ExtractFilterBindings(*expression, bindings)) {
 				remaining_expressions.push_back(std::move(expression));
 			}
 		}
 		op.expressions = std::move(remaining_expressions);
 	}
 }
diff --git a/src/optimizer/join_order/relation_manager.cpp b/src/optimizer/join_order/relation_manager.cpp
index 8b6139caea7..fba675dc35b 100644
--- a/src/optimizer/join_order/relation_manager.cpp
+++ b/src/optimizer/join_order/relation_manager.cpp
@@ -852,12 +852,21 @@ bool RelationManager::ExtractBindings(const Expression &expression, unordered_se
 			return;
 		}
 	});
 	return can_reorder;
 }
 
+bool RelationManager::ExtractFilterBindings(const Expression &expression, unordered_set<RelationIndex> &bindings) {
+	const auto can_reorder = ExtractBindings(expression, bindings);
+	if (!bindings.empty()) {
+		return true;
+	}
+	// without bindings the filter can only move if it references no columns at all
+	return can_reorder && !ExpressionContainsColumnRef(expression);
+}
+
 JoinOrderExtraction RelationManager::ExtractEdges(LogicalOperator &op,
                                                   vector<reference<LogicalOperator>> &filter_operators,
                                                   JoinRelationSetManager &set_manager) {
 	// now that we know we are going to perform join ordering we actually extract the filters, eliminating duplicate
 	// filters in the process
 	JoinOrderExtraction result;
@@ -1053,21 +1062,21 @@ JoinOrderExtraction RelationManager::ExtractEdges(LogicalOperator &op,
 			}
 			}
 
 		} else {
 			// handle filters from logical filters
 			for (auto &expression : f_op.expressions) {
-				if (filter_set.find(*expression) == filter_set.end()) {
+				unordered_set<RelationIndex> bindings;
+				if (!ExtractFilterBindings(*expression, bindings)) {
+					// the filter is on a column that is not in our relational map. (example: limit_rownum)
+					// in this case we do not create a FilterInfo for it. (duckdb-internal/#1493)
+					continue;
+				}
+				// zero-column filters may be volatile, keep duplicates
+				if (bindings.empty() || filter_set.find(*expression) == filter_set.end()) {
 					filter_set.insert(*expression);
-					unordered_set<RelationIndex> bindings;
-					ExtractBindings(*expression, bindings);
-					if (bindings.empty()) {
-						// the filter is on a column that is not in our relational map. (example: limit_rownum)
-						// in this case we do not create a FilterInfo for it. (duckdb-internal/#1493)
-						continue;
-					}
 					auto &set = set_manager.GetJoinRelation(bindings);
 					auto filter_info = make_uniq<FilterInfo>(expression->Copy(), set, filters_and_bindings.size());
 					filter_info->from_logical_filter = true;
 					if (ExpressionBarrier::Contains(*expression)) {
 						// the barrier must not end up below one of the joins we are reordering
 						filter_info->must_remain_filter = true;

```

## Reproducer from the fix's own test

```sql
-- test/optimizer/joins/zero_column_filter_reconstruction.test
SET disabled_optimizers='filter_pushdown';
CREATE TABLE t0(c0 INTEGER);
INSERT INTO t0 VALUES (1);
CREATE TABLE t1(c0 INTEGER);
INSERT INTO t1 VALUES (1);
SELECT (SELECT 1) AS a0, (SELECT 2) AS a1 FROM t0 WHERE NULL;
SELECT (SELECT 1) AS a0, (SELECT 2) AS a1 FROM t0 WHERE random() > 2;
SELECT (SELECT 1) AS a0, (SELECT 2) AS a1 FROM t0 WHERE random() < 2;
SELECT count(*) FROM (SELECT (SELECT 1), (SELECT 2) FROM t0 JOIN t1 ON t0.c0 = t1.c0 WHERE NULL);
SELECT count(*) FROM (SELECT (SELECT 1), (SELECT 2) FROM t0 LEFT JOIN t1 ON t0.c0 = t1.c0 WHERE NULL);
SELECT count(*) FROM (SELECT (SELECT 1), (SELECT 2) FROM t0 WHERE c0 IN (SELECT c0 FROM t1) AND NULL);
CREATE SEQUENCE root_filter_calls;
SELECT a, b
FROM (VALUES (1)) l(a), (VALUES (2)) r(b)
WHERE nextval('root_filter_calls') > 0;
SELECT currval('root_filter_calls');
CREATE SEQUENCE duplicate_filter_calls;
SELECT a, b
FROM (VALUES (1)) l(a), (VALUES (2)) r(b)
WHERE nextval('duplicate_filter_calls') > 0 AND nextval('duplicate_filter_calls') > 0;
SELECT currval('duplicate_filter_calls');
```

## Your task (this bug is KNOWN and exists in 2.0.0 - do not report it)
1. State the bug pattern in one sentence (what assumption did the old code make?).
2. `grep -rn` the pattern in `$FUZZ_WORK/duckdb/src` and `extension/`: list other sites with the same assumption.
3. For each site, write a reproducer (or a generator, see docs/AGENT_BRIEF.md) and run it with `verify_finding.py --dev-site $FUZZ_WORK/dev-snapshot`. Fails on 2.0.0 AND on the snapshot = sibling or incomplete fix.
4. Write what you tried and the result in AGENT_NOTES.md, including the negative results.