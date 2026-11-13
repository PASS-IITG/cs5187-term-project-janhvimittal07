# Dossier dcec7e4d9d - fixes a surfaced 'Invalid Input Error: Division by zero' hit by the optimizer / constant-folder

* date 2026-10-15; branch origin/main; PR unknown
* source files changed: src/function/function.cpp, src/include/duckdb/function/function.hpp, src/include/duckdb/main/config.hpp, src/optimizer/rule/case_simplification.cpp
* tests added/changed: test/optimizer/case_simplification_errors.test, test/sql/function/generic/constant_argument_errors.test

## The fix (diff, pre-fix lines start with `-`)

```diff
diff --git a/src/function/function.cpp b/src/function/function.cpp
index 103cca8a4e7..9d059ea8ef2 100644
--- a/src/function/function.cpp
+++ b/src/function/function.cpp
@@ -668,13 +668,17 @@ optional<Value> BindFunctionInput::TryGetConstant(idx_t arg_idx) const {
 	if (IsUnresolvedArgument(expr)) {
 		return {};
 	}
 	if (!expr.IsFoldable()) {
 		return {};
 	}
-	return ExpressionExecutor::EvaluateScalar(context, expr);
+	Value result;
+	if (!ExpressionExecutor::TryEvaluateScalar(context, expr, result)) {
+		return {};
+	}
+	return result;
 }
 
 optional<Value> BindFunctionInput::TryGetConstant(const Identifier &name) const {
 	const auto arg_idx = GetArgumentIndex(name);
 	if (arg_idx.IsValid()) {
 		return TryGetConstant(arg_idx.GetIndex());
diff --git a/src/include/duckdb/function/function.hpp b/src/include/duckdb/function/function.hpp
index affaae7c9b9..49fba11656e 100644
--- a/src/include/duckdb/function/function.hpp
+++ b/src/include/duckdb/function/function.hpp
@@ -713,16 +713,17 @@ public:
 	}
 	DUCKDB_API Value GetNonNullConstant(const Identifier &name) const {
 		return GetConstant(name, false);
 	}
 
 	//! Try to get the constant value of an argument.
-	//! Never throws: returns none if the argument...
+	//! Returns none if the argument...
 	//! - is not constant (unresolved parameter or a non-foldable expression)
 	//! - index is out of range
 	//! - was not found when looking up by name
+	//! - raises an evaluation error (internal errors still propagate)
 	//! Use this when a non-constant argument should fall back to the runtime value instead of being an error.
 	DUCKDB_API optional<Value> TryGetConstant(idx_t arg_idx) const;
 	DUCKDB_API optional<Value> TryGetConstant(const Identifier &name) const;
 
 private:
 	//! Resolve a named argument to its position, or an empty optional_idx if no argument with that name was provided.
diff --git a/src/include/duckdb/main/config.hpp b/src/include/duckdb/main/config.hpp
index 29c921acab4..757da6cd52c 100644
--- a/src/include/duckdb/main/config.hpp
+++ b/src/include/duckdb/main/config.hpp
@@ -188,13 +188,13 @@ public:
 			scans = std::move(other.scans);
 		}
 		return *this;
 	}
 
 	template <class... ARGS>
-	void emplace_back(ARGS &&...args) { // NOLINT
+	void emplace_back(ARGS &&... args) { // NOLINT
 		auto scan = make_shared_ptr<ReplacementScan>(std::forward<ARGS>(args)...);
 		lock_guard<mutex> guard(lock);
 		scans.push_back(std::move(scan));
 	}
 	void push_back(ReplacementScan scan) { // NOLINT
 		auto shared_scan = make_shared_ptr<ReplacementScan>(std::move(scan));
diff --git a/src/optimizer/rule/case_simplification.cpp b/src/optimizer/rule/case_simplification.cpp
index 0fdaff5b7a8..290e97a7694 100644
--- a/src/optimizer/rule/case_simplification.cpp
+++ b/src/optimizer/rule/case_simplification.cpp
@@ -16,13 +16,16 @@ unique_ptr<Expression> CaseSimplificationRule::Apply(LogicalOperator &op, vector
 	auto &root = bindings[0].get().Cast<BoundCaseExpression>();
 	for (idx_t i = 0; i < root.CaseChecksMutable().size(); i++) {
 		auto &case_check = root.CaseChecksMutable()[i];
 		if (case_check.when_expr->IsFoldable()) {
 			// the WHEN check is a foldable expression
 			// use an ExpressionExecutor to execute the expression
-			auto constant_value = ExpressionExecutor::EvaluateScalar(GetContext(), *case_check.when_expr);
+			Value constant_value;
+			if (!ExpressionExecutor::TryEvaluateScalar(GetContext(), *case_check.when_expr, constant_value)) {
+				continue;
+			}
 
 			// fold based on the constant condition
 			auto condition = constant_value.DefaultCastAs(LogicalType::BOOLEAN);
 			if (condition.IsNull() || !BooleanValue::Get(condition)) {
 				// the condition is always false: remove this case check
 				root.CaseChecksMutable().erase_at(i);

```

## Reproducer from the fix's own test

```sql
-- test/optimizer/case_simplification_errors.test
SELECT CASE WHEN count(*) = 1 THEN 'first' WHEN 1 // 0 = 0 THEN 'unreachable' ELSE 'last' END;
SELECT COALESCE(1, CASE WHEN 1 // 0 = 0 THEN count(*) ELSE 2 END);
SELECT CASE WHEN count(*) = 1 THEN [42] ELSE CASE WHEN 1 // 0 = 0 THEN [1] ELSE [2] END END;
SELECT CASE WHEN count(*) = 1 THEN 42 WHEN CAST('not-a-boolean' AS BOOLEAN) THEN 1 ELSE 2 END;
SELECT CASE WHEN NULL THEN 0 WHEN count(*) = 1 THEN 42 WHEN 1 // 0 = 0 THEN 1 WHEN false THEN 2 WHEN true THEN 3 ELSE 4 END;
SELECT CASE WHEN NULL THEN 0 WHEN count(*) = 0 THEN 42 WHEN 1 // 0 = 0 THEN 1 WHEN false THEN 2 WHEN true THEN 3 ELSE 4 END;
SELECT CASE WHEN count(*) = 0 THEN 42 WHEN CAST('not-a-boolean' AS BOOLEAN) THEN 1 ELSE 2 END;
SELECT TRY(CASE WHEN cnt = 0 THEN 42 WHEN 1 // 0 = 0 THEN 1 ELSE 2 END) FROM (SELECT count(*) AS cnt);

-- test/sql/function/generic/constant_argument_errors.test
SELECT COALESCE(42::{type}, -(1::{type} // 0::{type}));
SELECT CASE WHEN count(*) = 1 THEN 42::{type} ELSE -(1::{type} // 0::{type}) END;
SELECT TRY(-(1::{type} // 0::{type}));
SELECT COALESCE(NULL::{type}, -(1::{type} // 0::{type}));
SELECT COALESCE(42, -CAST('not-an-integer' AS INTEGER));
SELECT -CAST('not-an-integer' AS INTEGER);
SELECT COALESCE('kept', substring('abc', 1 // 0));
SELECT COALESCE(42, list_extract([1, 2], 1 // 0));
SELECT substring('abc', 1 // 0);
SELECT list_extract([1, 2], 1 // 0);
```

## Your task (this bug is KNOWN and exists in 2.0.0 - do not report it)
1. State the bug pattern in one sentence (what assumption did the old code make?).
2. `grep -rn` the pattern in `$FUZZ_WORK/duckdb/src` and `extension/`: list other sites with the same assumption.
3. For each site, write a reproducer (or a generator, see docs/AGENT_BRIEF.md) and run it with `verify_finding.py --dev-site $FUZZ_WORK/dev-snapshot`. Fails on 2.0.0 AND on the snapshot = sibling or incomplete fix.
4. Write what you tried and the result in AGENT_NOTES.md, including the negative results.