"""断言引擎的单元测试。"""

import unittest

from tests.helpers import ROOT  # noqa: F401  确保仓库根在 sys.path

from engine import assertion


class PickTest(unittest.TestCase):
    def test_nested_dict(self):
        self.assertEqual(assertion.pick({"data": {"user": {"id": 7}}}, "data.user.id"), 7)

    def test_list_index(self):
        self.assertEqual(assertion.pick({"items": [{"sku": "A1"}]}, "items.0.sku"), "A1")

    def test_missing_returns_none(self):
        self.assertIsNone(assertion.pick({"a": 1}, "b.c"))
        self.assertIsNone(assertion.pick(None, "a"))

    def test_index_out_of_range(self):
        self.assertIsNone(assertion.pick({"items": [1]}, "items.3"))

    def test_digit_key_on_dict(self):
        self.assertEqual(assertion.pick({"0": "x"}, "0"), "x")


class OperatorTest(unittest.TestCase):
    def check(self, op, actual, value, expected_ok):
        rows = assertion.run_checks(200, {"v": actual}, [{"field": "v", "op": op, "value": value}])
        self.assertEqual(rows[0].ok, expected_ok, rows[0])

    def test_eq(self):
        self.check("eq", 0, 0, True)
        self.check("eq", 0, "0", False)

    def test_not_null(self):
        self.check("not_null", "x", None, True)
        self.check("not_null", "", None, False)
        self.check("not_null", None, None, False)

    def test_contains_string_and_list(self):
        self.check("contains", "test001", "test", True)
        self.check("contains", ["admin", "tester"], "admin", True)
        self.check("contains", ["admin"], "guest", False)
        self.check("contains", 123, "1", False)

    def test_gt(self):
        self.check("gt", 30, 18, True)
        self.check("gt", 18, 18, False)
        self.check("gt", "30", 18, False)

    def test_in(self):
        self.check("in", "test001", ["test001", "guest"], True)
        self.check("in", "other", ["test001"], False)
        self.check("in", "test001", "test001", False)

    def test_regex(self):
        self.check("regex", "test001", r"^test\d+$", True)
        self.check("regex", "guest", r"^test\d+$", False)
        self.check("regex", 1, r"\d", False)      # 非字符串直接不中
        self.check("regex", "x", r"(", False)      # 非法正则不炸

    def test_len(self):
        self.check("len", [1, 2], 2, True)
        self.check("len", "abc", 2, False)
        self.check("len", 5, 2, False)

    def test_type_strict_on_bool(self):
        self.check("type", True, "int", False)     # bool 不是 int
        self.check("type", 1, "int", True)
        self.check("type", 1.0, "float", True)
        self.check("type", None, "null", True)
        self.check("type", [], "list", True)
        self.check("type", {}, "dict", True)
        self.check("type", "x", "nonsense", False)

    def test_status_code_field(self):
        rows = assertion.run_checks(404, {"code": 404},
                                    [{"field": "status_code", "op": "eq", "value": 404}])
        self.assertTrue(rows[0].ok)
        self.assertEqual(rows[0].op, "eq")

    def test_body_none_still_checks_status(self):
        rows = assertion.run_checks(200, None, [{"field": "status_code", "op": "eq", "value": 200}])
        self.assertTrue(rows[0].ok)
        rows = assertion.run_checks(200, None, [{"field": "code", "op": "eq", "value": 0}])
        self.assertFalse(rows[0].ok)


class ValidateTest(unittest.TestCase):
    def test_unknown_op_reported(self):
        problem = assertion.validate_item({"field": "code", "op": "eqq", "value": 0})
        self.assertIn("eqq", problem)
        self.assertIn("eq", problem)

    def test_missing_value_reported(self):
        self.assertIsNotNone(assertion.validate_item({"field": "code", "op": "eq"}))
        self.assertIsNone(assertion.validate_item({"field": "token", "op": "not_null"}))
        self.assertIsNotNone(assertion.validate_item({"field": "", "op": "eq", "value": 1}))
        self.assertIsNotNone(assertion.validate_item("不是字典"))

    def test_not_dict_uses_op_table(self):
        with self.assertRaises(ValueError):
            assertion.run_checks(200, {}, [{"field": "code", "op": "nope", "value": 1}])


class DescribeTest(unittest.TestCase):
    def test_expect_summary(self):
        text = assertion.describe_expect([
            {"field": "code", "op": "eq", "value": 0},
            {"field": "token", "op": "not_null"},
            {"field": "age", "op": "gt", "value": 18},
        ])
        self.assertEqual(text, "code=0，token非空，age>18")

    def test_actual_and_bad(self):
        rows = assertion.run_checks(200, {"code": 1},
                                    [{"field": "code", "op": "eq", "value": 0}])
        self.assertEqual(assertion.describe_actual(rows), "code=1")
        self.assertIn("期望 0", assertion.describe_bad(rows[0]))
        self.assertEqual(assertion.describe_bad(None), "")


if __name__ == "__main__":
    unittest.main()
