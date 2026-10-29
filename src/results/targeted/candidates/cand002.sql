CREATE TABLE t(a INT, b VARCHAR, c INT[]);
INSERT INTO t VALUES (1,'x',[1,2]), (2,'x',[]), (NULL,'y',NULL), (3,'z',[3]), (3,'z',[3,NULL]), (0,NULL,[0]);
CREATE TABLE t2(a INT, b VARCHAR, s STRUCT(x INT, y VARCHAR));
INSERT INTO t2 VALUES (1,'x',{'x':1,'y':'p'}), (2,'y',{'x':2,'y':NULL}), (NULL,NULL,NULL);
SELECT a, b FROM (SELECT a, b FROM (SELECT a, b FROM (SELECT (SELECT max(t.a) FROM t WHERE t.b = q.b) AS a, b FROM (SELECT a, b FROM (SELECT a, b FROM t WHERE a IS NULL OR a > 1) q QUALIFY row_number() OVER (PARTITION BY b ORDER BY a) = 1) q) l EXCEPT ALL SELECT a, b FROM (SELECT (SELECT max(t.a) FROM t WHERE t.b = q.b) AS a, b FROM (SELECT a, b FROM t WHERE a IS NULL OR a > 1) q) r) q ORDER BY ALL) q GROUP BY GROUPING SETS ((a, b), (b), ());
