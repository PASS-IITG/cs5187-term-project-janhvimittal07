# Dossier 2fdef2acbb - Raise an error when date_trunc of a fixed-width part overflows

* date 2026-10-14; branch origin/main; PR unknown
* source files changed: extension/core_functions/scalar/date/date_trunc.cpp
* tests added/changed: test/sql/function/timestamp/date_trunc_fixed_overflow.test

## The fix (diff, pre-fix lines start with `-`)

```diff
diff --git a/extension/core_functions/scalar/date/date_trunc.cpp b/extension/core_functions/scalar/date/date_trunc.cpp
index 9998eb3d5e3..7547113cd43 100644
--- a/extension/core_functions/scalar/date/date_trunc.cpp
+++ b/extension/core_functions/scalar/date/date_trunc.cpp
@@ -1,10 +1,11 @@
 #include "core_functions/scalar/date_functions.hpp"
 #include "duckdb/common/enums/date_part_specifier.hpp"
 #include "duckdb/common/exception.hpp"
 #include "duckdb/common/operator/cast_operators.hpp"
+#include "duckdb/common/operator/multiply.hpp"
 #include "duckdb/common/types/date.hpp"
 #include "duckdb/common/types/time.hpp"
 #include "duckdb/common/types/timestamp.hpp"
 #include "duckdb/common/types/value.hpp"
 #include "duckdb/execution/expression_executor.hpp"
 
@@ -99,13 +100,17 @@ struct DateTrunc {
 	// (second, minute, hour, day). Variable-length units (month, year, etc.)
 	// must use the calendar-decomposition path above.
 	static inline timestamp_t TruncFixed(timestamp_t ts, int64_t interval_us) {
 		const int64_t v = ts.value;
 		// Round towards negative infinity instead of 0
 		const int64_t q = v / interval_us - (v % interval_us != 0 && v < 0 ? 1 : 0);
-		return timestamp_t(q * interval_us);
+		int64_t result;
+		if (!TryMultiplyOperator::Operation(q, interval_us, result) || result == NumericLimits<int64_t>::Minimum()) {
+			throw ConversionException("Date out of range in timestamp conversion");
+		}
+		return timestamp_t(result);
 	}
 
 	struct HourOperator {
 		template <class TA, class TR>
 		static inline TR Operation(TA input) {
 			return TruncFixed(input, Interval::MICROS_PER_HOUR);

```

## Reproducer from the fix's own test

```sql
-- test/sql/function/timestamp/date_trunc_fixed_overflow.test
SELECT date_trunc('{part}', make_timestamp(-9223372036854775806));
SELECT date_trunc('day', TIMESTAMP '2020-01-01 12:34:56');
SELECT date_trunc('hour', TIMESTAMP '1900-01-01 12:34:56');
```

## Your task (this bug is KNOWN and exists in 2.0.0 - do not report it)
1. State the bug pattern in one sentence (what assumption did the old code make?).
2. `grep -rn` the pattern in `$FUZZ_WORK/duckdb/src` and `extension/`: list other sites with the same assumption.
3. For each site, write a reproducer (or a generator, see docs/AGENT_BRIEF.md) and run it with `verify_finding.py --dev-site $FUZZ_WORK/dev-snapshot`. Fails on 2.0.0 AND on the snapshot = sibling or incomplete fix.
4. Write what you tried and the result in AGENT_NOTES.md, including the negative results.