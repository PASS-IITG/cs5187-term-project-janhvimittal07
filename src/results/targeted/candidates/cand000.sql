SET enable_external_access=false;
SELECT assert_true(true);
SELECT assert_true(true, ['a','b']);
SELECT assert_true(1 = 1, 'never shown');
SELECT assert_true(false);
SELECT assert_true(NULL);
SELECT assert_true(false, 'my custom message');
SELECT assert_true(false, NULL);
SELECT assert_true(NULL, NULL);
SELECT assert_true(i < 3, 'row ' || i || ' is too large') FROM range(5) t(i);
SELECT assert_true(i < 10) FROM range(3) t(i);

