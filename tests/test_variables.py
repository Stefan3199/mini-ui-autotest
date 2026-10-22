"""变量替换的单元测试。"""

import unittest

from tests.helpers import ROOT  # noqa: F401  确保仓库根在 sys.path

from engine import variables


class RenderTest(unittest.TestCase):
    def test_whole_string_keeps_type(self):
        self.assertEqual(variables.render("${want}", {"want": 0}), 0)
        self.assertIs(variables.render("${flag}", {"flag": True}), True)
        self.assertEqual(variables.render("${items}", {"items": [1, 2]}), [1, 2])

    def test_embedded_becomes_string(self):
        self.assertEqual(variables.render("Bearer ${token}", {"token": "abc"}), "Bearer abc")
        self.assertEqual(variables.render("${n} 条", {"n": 3}), "3 条")

    def test_plain_string_untouched(self):
        self.assertEqual(variables.render("没有变量", {}), "没有变量")

    def test_missing_raises(self):
        with self.assertRaises(variables.MissingVar) as ctx:
            variables.render("${token}", {})
        self.assertEqual(ctx.exception.name, "token")

    def test_nested_structures(self):
        obj = {"params": {"user": "${user}", "pwd": "x"}, "list": ["${user}", "固定"]}
        rendered = variables.render(obj, {"user": "test001"})
        self.assertEqual(rendered["params"]["user"], "test001")
        self.assertEqual(rendered["list"], ["test001", "固定"])

    def test_env_variable(self):
        environ = {"HOME_DIR": "/tmp/home"}
        self.assertEqual(variables.render("${ENV:HOME_DIR}", {}, environ), "/tmp/home")
        self.assertEqual(variables.render("${ENV:HOME_DIR}/x", {}, environ), "/tmp/home/x")

    def test_env_missing_raises(self):
        with self.assertRaises(variables.MissingVar):
            variables.render("${ENV:NOT_SET_ANYWHERE}", {}, {})

    def test_does_not_mutate_input(self):
        obj = {"a": "${v}"}
        variables.render(obj, {"v": 1})
        self.assertEqual(obj, {"a": "${v}"})


class FindPlaceholdersTest(unittest.TestCase):
    def test_paths_and_names(self):
        case = {"params": {"user": "${user}"}, "headers": {"X-Token": "${token}"}}
        found = variables.find_placeholders(case)
        self.assertIn(("params.user", "user"), found)
        self.assertIn(("headers.X-Token", "token"), found)

    def test_env_placeholder_name(self):
        found = variables.find_placeholders({"headers": {"A": "${ENV:HOME}"}})
        self.assertEqual(found, [("headers.A", "ENV:HOME")])

    def test_lists_get_index_paths(self):
        found = variables.find_placeholders({"list": ["${a}", "${b}"]})
        self.assertEqual(found, [("list.0", "a"), ("list.1", "b")])

    def test_no_placeholder(self):
        self.assertEqual(variables.find_placeholders({"a": 1}), [])


if __name__ == "__main__":
    unittest.main()
