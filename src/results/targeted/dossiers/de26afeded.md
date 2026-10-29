# Dossier de26afeded - Fix sign of the Kahan compensation term in fsum/favg combine and favg finalize

* date 2026-10-06; branch origin/main; PR unknown
* source files changed: extension/core_functions/aggregate/algebraic/avg.cpp, extension/core_functions/include/core_functions/aggregate/sum_helpers.hpp
* tests added/changed: test/sql/aggregate/aggregates/test_kahan_combine.test

## The fix (diff, pre-fix lines start with `-`)

```diff
diff --git a/extension/core_functions/aggregate/algebraic/avg.cpp b/extension/core_functions/aggregate/algebraic/avg.cpp
index 6d5b67faf36..6aded168177 100644
--- a/extension/core_functions/aggregate/algebraic/avg.cpp
+++ b/extension/core_functions/aggregate/algebraic/avg.cpp
@@ -45,13 +45,13 @@ struct KahanAvgState {
 	double value;
 	double err;
 
 	void Combine(const KahanAvgState &other) {
 		this->count += other.count;
 		KahanAddInternal(other.value, this->value, this->err);
-		KahanAddInternal(other.err, this->value, this->err);
+		KahanAddInternal(-other.err, this->value, this->err);
 	}
 };
 
 struct AverageDecimalBindData : public FunctionData {
 	explicit AverageDecimalBindData(double scale) : scale(scale) {
 	}
@@ -179,13 +179,13 @@ struct NumericAverageOperation : public BaseSumOperation<AverageSetOperation, Re
 struct KahanAverageOperation : public BaseSumOperation<AverageSetOperation, KahanAdd> {
 	template <class T, class STATE>
 	static void Finalize(STATE &state, T &target, AggregateFinalizeData &finalize_data) {
 		if (state.count == 0) {
 			finalize_data.ReturnNull();
 		} else {
-			target = (state.value / state.count) + (state.err / state.count);
+			target = (state.value / state.count) - (state.err / state.count);
 		}
 	}
 };
 
 struct IntervalAverageOperation : public BaseSumOperation<AverageSetOperation, IntervalAdd> {
 	template <class STATE, class OP>
diff --git a/extension/core_functions/include/core_functions/aggregate/sum_helpers.hpp b/extension/core_functions/include/core_functions/aggregate/sum_helpers.hpp
index d6409dd9c63..7c35853aed7 100644
--- a/extension/core_functions/include/core_functions/aggregate/sum_helpers.hpp
+++ b/extension/core_functions/include/core_functions/aggregate/sum_helpers.hpp
@@ -97,13 +97,13 @@ struct KahanSumState {
 	double err;
 	bool is_set;
 
 	void Combine(const KahanSumState &other) {
 		this->is_set = other.is_set || this->is_set;
 		KahanAddInternal(other.value, this->value, this->err);
-		KahanAddInternal(other.err, this->value, this->err);
+		KahanAddInternal(-other.err, this->value, this->err);
 	}
 };
 
 struct RegularAdd {
 	template <class STATE, class T>
 	static void AddNumber(STATE &state, T input) {

```

## Reproducer from the fix's own test

```sql
-- test/sql/aggregate/aggregates/test_kahan_combine.test
CREATE TABLE doubles AS SELECT * FROM (VALUES (9007199254740992.0::DOUBLE), (1.0::DOUBLE)) t(x);
SELECT printf('%.17g', fsum(x)),
	printf('%.17g', kahan_sum(x)),
	printf('%.17g', sumkahan(x)),
	printf('%.17g', sum(x)),
	printf('%.17g', favg(x)),
	printf('%.17g', avg(x))
FROM doubles;
SELECT printf('%.17g', fsum(-x)), printf('%.17g', favg(-x))
FROM doubles;
SELECT printf('%.17g', fsum(x)), printf('%.17g', favg(x))
FROM (SELECT x FROM doubles UNION ALL SELECT 1.0::DOUBLE);
SELECT g, printf('%.17g', fsum(x)), printf('%.17g', favg(x))
FROM (SELECT g, x * (CASE WHEN g = 1 THEN 1 ELSE -1 END) AS x FROM doubles, range(1, 3) r(g))
GROUP BY g
ORDER BY g;
SELECT printf('%.17g', finalize(combine(
		(SELECT fsum(x) EXPORT_STATE FROM doubles),
		(SELECT fsum(x) EXPORT_STATE FROM doubles WHERE false)))),
	printf('%.17g', finalize(combine(
		(SELECT favg(x) EXPORT_STATE FROM doubles),
		(SELECT favg(x) EXPORT_STATE FROM doubles WHERE false))));
```

## Your task (this bug is KNOWN and exists in 2.0.0 - do not report it)
1. State the bug pattern in one sentence (what assumption did the old code make?).
2. `grep -rn` the pattern in `$FUZZ_WORK/duckdb/src` and `extension/`: list other sites with the same assumption.
3. For each site, write a reproducer (or a generator, see docs/AGENT_BRIEF.md) and run it with `verify_finding.py --dev-site $FUZZ_WORK/dev-snapshot`. Fails on 2.0.0 AND on the snapshot = sibling or incomplete fix.
4. Write what you tried and the result in AGENT_NOTES.md, including the negative results.