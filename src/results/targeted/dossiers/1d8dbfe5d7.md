# Dossier 1d8dbfe5d7 - Fix prepared statements and errors while CONNECT-ed, resolve types first when extracting a plan

* date 2026-10-21; branch origin/main; PR unknown
* source files changed: src/include/duckdb/main/active_query_context.hpp, src/include/duckdb/main/client_context.hpp, src/include/duckdb/main/prepared_statement.hpp, src/main/client_context.cpp, src/main/client_context_execution.cpp, src/main/prepared_statement.cpp, src/planner/binder/statement/bind_connect.cpp
* tests added/changed: test/sql/connect/connect_failure_keeps_local_transaction.test, test/sql/connect/connect_string_ephemeral.test, test/sql/connect/disconnect_aborted_transaction.test

## The fix (diff, pre-fix lines start with `-`)

```diff
diff --git a/src/include/duckdb/main/active_query_context.hpp b/src/include/duckdb/main/active_query_context.hpp
index f7c87d98bdc..9ab5ef6ee4d 100644
--- a/src/include/duckdb/main/active_query_context.hpp
+++ b/src/include/duckdb/main/active_query_context.hpp
@@ -44,12 +44,14 @@ public:
 	//! Whether the statement whose rows the result carries has started
 	bool result_statement_started = false;
 	//! The handle of a statement that expands into several, which the statement producing the result fills in
 	optional_ptr<QueryResult> handle;
 	//! Whether the statements run inside the submission, where a result collector may build its own result object
 	bool inside_submission = false;
+	//! Whether the statement runs on the CONNECT-ed database, outside the local transaction
+	bool outside_local_transaction = false;
 
 public:
 	void SetOpenResult(BaseQueryResult &result) {
 		open_result = &result;
 	}
 	bool IsOpenResult(BaseQueryResult &result) {
diff --git a/src/include/duckdb/main/client_context.hpp b/src/include/duckdb/main/client_context.hpp
index 31fdd96c30b..c4e4601ebdb 100644
--- a/src/include/duckdb/main/client_context.hpp
+++ b/src/include/duckdb/main/client_context.hpp
@@ -104,12 +104,16 @@ public:
 	    "connection has an open result; drain or destroy it before starting a new query";
 	//! The error of a query that ran past max_execution_time
 	static constexpr const char *QUERY_DEADLINE_ERROR = "Query exceeded maximum execution time";
 	//! The error of a statement that expands into several, given parameters
 	static constexpr const char *EXPANDING_PARAMETERS_ERROR =
 	    "parameters are not supported for a statement that expands into several";
+	//! The error of a prepared statement with parameters, while CONNECT-ed
+	static constexpr const char *CONNECTED_PARAMETERS_ERROR =
+	    "Parameterized prepared statements cannot be executed while CONNECT-ed; DISCONNECT first, or run the SQL as a "
+	    "fresh statement to route through the CONNECT binding";
 
 public:
 	DUCKDB_API explicit ClientContext(shared_ptr<DatabaseInstance> db);
 	DUCKDB_API ~ClientContext();
 
 	//! The database that this client is connected to
diff --git a/src/include/duckdb/main/prepared_statement.hpp b/src/include/duckdb/main/prepared_statement.hpp
index b99700d0ad7..d0c12139bed 100644
--- a/src/include/duckdb/main/prepared_statement.hpp
+++ b/src/include/duckdb/main/prepared_statement.hpp
@@ -40,19 +40,20 @@ struct PreparedStatementInfo {
 	//! The expected type of each parameter whose type could be resolved when preparing
 	identifier_map_t<LogicalType> parameter_types;
 };
 
 //! A handle to a prepared statement that lives in the client context. The statement itself is prepared through
 //! `PREPARE <name> AS ...` and executed through `EXECUTE <name>(...)` - this class only holds the name.
-//! A statement that expands into several is not prepared in the client context: each execution submits it anew.
+//! A statement that expands into several, or one prepared while CONNECT-ed, is not prepared in the client context,
+//! and each execution submits it anew.
 class PreparedStatement {
 public:
 	//! Create a handle to the prepared statement with the given name in the client context
 	DUCKDB_API PreparedStatement(const shared_ptr<ClientContext> &context, string name, string query,
 	                             PreparedStatementInfo info);
-	//! Create a handle to a statement that does not expand into exactly one, whose result is known only at execution
+	//! Create a handle to a statement that each execution submits anew, whose result is known only at execution
 	DUCKDB_API PreparedStatement(const shared_ptr<ClientContext> &context, unique_ptr<SQLStatement> statement);
 	//! Create a prepared statement that was not successfully prepared
 	DUCKDB_API explicit PreparedStatement(ErrorData error);
 
 	DUCKDB_API ~PreparedStatement();
 
@@ -208,18 +209,18 @@ private:
 	//! Whether or not the statement was successfully prepared
 	bool success;
 	//! The error message (if success = false)
 	ErrorData error;
 	//! The metadata of the statement, as it was prepared
 	PreparedStatementInfo info;
-	//! The statement as the user wrote it, when it does not expand into exactly one
-	unique_ptr<SQLStatement> expanding_statement;
+	//! The statement as the user wrote it, when each execution submits it anew
+	unique_ptr<SQLStatement> written_statement;
 
 private:
-	//! Create the statement that runs this prepared statement with the given values: `EXECUTE <name>(...)`, or a
-	//! copy of the statement that expands, whose values are handed to the submission in the parameters
+	//! Create the statement that runs this prepared statement with the given values, `EXECUTE <name>(...)` or a copy
+	//! of the written statement, whose values are handed to the submission in the parameters
 	unique_ptr<SQLStatement> CreateStatement(identifier_map_t<BoundParameterData> &named_values,
 	                                         QueryParameters &parameters) const;
 
 	unique_ptr<QueryResult> SubmitRecursive(vector<Value> &values) {
 		return Submit(values);
 	}
diff --git a/src/main/client_context.cpp b/src/main/client_context.cpp
index 033e81aff24..5acb26cc4d6 100644
--- a/src/main/client_context.cpp
+++ b/src/main/client_context.cpp
@@ -33,12 +33,13 @@
 #include "duckdb/parser/parsed_data/create_function_info.hpp"
 #include "duckdb/parser/parser.hpp"
 #include "duckdb/parser/peg/compiled_grammar.hpp"
 #include "duckdb/parser/query_node/select_node.hpp"
 #include "duckdb/parser/statement/execute_statement.hpp"
 #include "duckdb/parser/statement/explain_statement.hpp"
+#include "duckdb/parser/statement/passthrough_statement.hpp"
 #include "duckdb/parser/statement/prepare_statement.hpp"
 #include "duckdb/parser/statement/relation_statement.hpp"
 #include "duckdb/parser/statement/select_statement.hpp"
 #include "duckdb/planner/binder.hpp"
 #include "duckdb/planner/logical_plan_verifier.hpp"
 #include "duckdb/planner/planner.hpp"
@@ -243,13 +244,15 @@ void ClientContext::ApplyInvalidationPolicy(const ErrorData &error) {
 		// standard exceptions don't invalidate the transaction
 		invalidates_transaction = false;
 	} else if (Exception::InvalidatesDatabase(error.Type())) {
 		auto &db_instance = DatabaseInstance::GetDatabase(*this);
 		ValidChecker::Invalidate(db_instance, error.RawMessage());
 	}
-	if (invalidates_transaction && HasActiveTransaction()) {
+	// a statement issued while CONNECT-ed that did not run locally is not part of the local transaction
+	const bool outside_local_transaction = active_query ? active_query->outside_local_transaction : is_connected;
+	if (invalidates_transaction && HasActiveTransaction() && !outside_local_transaction) {
 		ValidChecker::Invalidate(ActiveTransaction(), error.RawMessage());
 	}
 }
 
 template <class T>
 unique_ptr<T> ClientContext::ErrorResult(ErrorData error, const string &query) {
@@ -491,17 +494,17 @@ unique_ptr<LogicalOperator> ClientContext::ExtractPlan(const string &query) {
 
 		plan = std::move(planner.plan);
 
 		Optimizer optimizer(*planner.binder, *this);
 		plan = optimizer.Optimize(std::move(plan));
 
-		ColumnBindingResolver resolver;
 		LogicalPlanVerifier::Verify(*this, *plan);
-		resolver.VisitOperator(*plan);
-
+		// the resolver checks the column references against the operator types, which the optimizer leaves stale
 		plan->ResolveOperatorTypes();
+		ColumnBindingResolver resolver;
+		resolver.VisitOperator(*plan);
 	});
 	return plan;
 }
 
 //! Snapshot the metadata that the PreparedStatement handle exposes to the user
 static PreparedStatementInfo GetPreparedStatementInfo(PreparedStatementData &data) {
@@ -521,12 +524,26 @@ static PreparedStatementInfo GetPreparedStatementInfo(PreparedStatementData &dat
 	}
 	return info;
 }
 
 unique_ptr<PreparedStatement> ClientContext::PrepareInternal(ClientContextLock &lock,
                                                              unique_ptr<SQLStatement> statement) {
+	if (is_connected) {
+		// the connected database runs the statement as written, at each execution
+		if (!statement->named_param_map.empty()) {
+			throw InvalidInputException(CONNECTED_PARAMETERS_ERROR);
+		}
+		if (statement->type != StatementType::DISCONNECT_STATEMENT) {
+			// it runs on the connected database only, also when parsed before CONNECT
+			auto passthrough = make_uniq<PassthroughStatement>();
+			passthrough->query = std::move(statement->query);
+			passthrough->stmt_location = statement->stmt_location;
+			statement = std::move(passthrough);
+		}
+		return make_uniq<PreparedStatement>(shared_from_this(), std::move(statement));
+	}
 	// Only a pragma or a multi-statement can expand, and only one that does not expand into exactly one statement
 	// keeps the statement as written, to expand it again at each execution
 	unique_ptr<SQLStatement> written_statement;
 	if (statement->type == StatementType::PRAGMA_STATEMENT || statement->type == StatementType::MULTI_STATEMENT) {
 		written_s
```

## Reproducer from the fix's own test

```sql
-- test/sql/connect/connect_failure_keeps_local_transaction.test
SET GLOBAL quack_prepare_inline_rows = 0;
CREATE TABLE local_t AS SELECT 1 AS i;
INSERT INTO local_t VALUES (2);
CREATE GRANT;
SELECT CASE WHEN i < 3 THEN i ELSE error('late failure') END FROM range(10) t(i);
SELECT 'unterminated;
SELECT 42;
SELECT sum(i) FROM local_t;
SELECT sum(i) FROM local_t;
INSERT INTO local_t VALUES (3);
CREATE GRANT;
SELECT 42;
SELECT 42;
SELECT sum(i) FROM local_t;
INSERT INTO local_t VALUES (4);
SELECT * FROM no_such_table;
SELECT sum(i) FROM local_t;

-- test/sql/connect/connect_string_ephemeral.test
SELECT count(*) FROM pragma_database_list WHERE name LIKE '__connect%';
SELECT error('boom');
```

## Your task (this bug is KNOWN and exists in 2.0.0 - do not report it)
1. State the bug pattern in one sentence (what assumption did the old code make?).
2. `grep -rn` the pattern in `$FUZZ_WORK/duckdb/src` and `extension/`: list other sites with the same assumption.
3. For each site, write a reproducer (or a generator, see docs/AGENT_BRIEF.md) and run it with `verify_finding.py --dev-site $FUZZ_WORK/dev-snapshot`. Fails on 2.0.0 AND on the snapshot = sibling or incomplete fix.
4. Write what you tried and the result in AGENT_NOTES.md, including the negative results.