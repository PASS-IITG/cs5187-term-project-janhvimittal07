FACTS (hunt1 remove_unused_columns r1, 2026-11-09). Both verified on 2.0.0 and local main d4eea3f208 (6 Nov); correct with SET disabled_optimizers='unused_columns'.
A. CREATE TABLE t(id INT,s STRUCT(a BIGINT,d INT)); INSERT INTO t VALUES (5,{'a':-9223372036854775808,'d':1}), (6,{'a':7,'d':1});
   SELECT id, CASE WHEN s.a>=0 THEN s.a::UBIGINT ELSE NULL END AS v FROM t ORDER BY id;
   -> Conversion Error (default); 5,NULL / 6,7 (optimizer off). EXPLAIN: scan projects s.a twice; cast appears pushed into the scan so it runs before the CASE guard. Flat column does not reproduce.
B. CREATE TABLE l(id INT,x DOUBLE[]); CREATE TABLE r(x DOUBLE[]); INSERT INTO l VALUES (1,[0.0::DOUBLE]); INSERT INTO r VALUES ([-0.0::DOUBLE]);
   SELECT l.id, count(*) FROM l JOIN r ON l.x=r.x GROUP BY l.id HAVING count(*) FILTER (WHERE r.x::VARCHAR <> l.x::VARCHAR)>0;
   -> no rows (default); 1,1 (optimizer off). EXPLAIN shows #1 != #1. Does not reproduce with a plain DOUBLE key. Cause not pinned down; related in spirit to 28559.
