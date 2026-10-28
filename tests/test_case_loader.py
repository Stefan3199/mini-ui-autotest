"""用例加载层的单元测试。"""

import tempfile
import unittest
from pathlib import Path

from tests.helpers import BASE_CONFIG, ROOT

from engine import case_loader
from engine.case_loader import CaseError


def valid(**overrides):
    case = {
        "name": "用例",
        "url": "/login",
        "method": "POST",
        "params": {},
        "expect": [{"field": "code", "op": "eq", "value": 0}],
    }
    case.update(overrides)
    return case


class ConfigTest(unittest.TestCase):
    def test_merge_is_deep(self):
        base = {"a": 1, "nest": {"x": 1, "y": 2}}
        override = {"nest": {"y": 20, "z": 30}}
        self.assertEqual(case_loader.merge(base, override),
                         {"a": 1, "nest": {"x": 1, "y": 20, "z": 30}})

    def test_defaults_and_env_merge(self):
        config = case_loader.load_config()
        self.assertEqual(config["base_url"], "http://127.0.0.1:9300")
        self.assertEqual(config["retry_count"], 3)
        self.assertIn("pwd", config["sensitive"])

        dev = case_loader.load_config("dev")
        self.assertEqual(dev["report_dir"], "report-dev")
        self.assertEqual(dev["timeout"], 6)
        self.assertEqual(dev["retry_count"], 3)  # 环境文件没写的沿用基础配置

    def test_unknown_env_raises(self):
        with self.assertRaises(CaseError) as ctx:
            case_loader.load_config("env-that-does-not-exist")
        self.assertIn("找不到环境配置", str(ctx.exception))

    def test_missing_base_url_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "config.yaml").write_text("report_dir: r\n", encoding="utf-8")
            with self.assertRaises(CaseError) as ctx:
                case_loader.load_config(base_dir=tmp)
            self.assertIn("base_url", str(ctx.exception))

    def test_bad_retry_count_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "config.yaml").write_text(
                "base_url: http://x\nretry_count: 0\n", encoding="utf-8")
            with self.assertRaises(CaseError):
                case_loader.load_config(base_dir=tmp)


class LoadCasesTest(unittest.TestCase):
    def test_total_is_23(self):
        loaded = case_loader.load_cases()
        self.assertEqual(len(loaded.cases), 26)
        self.assertEqual(loaded.warnings, [])

    def test_prepared_urls_and_index(self):
        loaded = case_loader.load_cases()
        for index, case in enumerate(loaded.cases, 1):
            self.assertEqual(case["index"], index)
            self.assertTrue(case["url"].startswith("http://127.0.0.1:9300/"), case["url"])
            self.assertIn("X-Token", case["headers"])

    def test_injected_config_is_used(self):
        config = dict(BASE_CONFIG, base_url="http://127.0.0.1:9999")
        loaded = case_loader.load_cases(config=config)
        self.assertTrue(all(case["url"].startswith("http://127.0.0.1:9999/")
                            for case in loaded.cases))

    def test_tag_filter_case_has_reasons(self):
        loaded = case_loader.load_cases()
        by_name = {case["name"]: case for case in loaded.cases}
        self.assertEqual(by_name["登录并存 token"]["serial_reasons"], ["save 变量"])
        self.assertIn("引用 ${token}", by_name["用存下的 token 请求"]["serial_reasons"])
        self.assertEqual(by_name["用存下的 token 请求"]["serial_reasons"][0], "引用 ${token}")


class PrepareTest(unittest.TestCase):
    def test_idempotent_and_does_not_mutate(self):
        case = valid()
        first = case_loader.prepare(case, BASE_CONFIG, {"X-Token": "d"}, 5)
        second = case_loader.prepare(first, BASE_CONFIG, {"X-Token": "d"}, 5)
        self.assertEqual(first["url"], "http://127.0.0.1:9300/login")
        self.assertEqual(second["url"], first["url"])
        self.assertEqual(case["url"], "/login")            # 入参没被改
        self.assertNotIn("headers", case)
        self.assertEqual(first["headers"], {"X-Token": "d"})

    def test_case_headers_override_default(self):
        prepared = case_loader.prepare(valid(headers={"X-Token": "mine"}),
                                       BASE_CONFIG, {"X-Token": "default"}, 5)
        self.assertEqual(prepared["headers"], {"X-Token": "mine"})

    def test_join_url(self):
        join = case_loader.join_url
        self.assertEqual(join("http://h:1", "/a"), "http://h:1/a")
        self.assertEqual(join("http://h:1/", "/a"), "http://h:1/a")
        self.assertEqual(join("http://h:1", "a"), "http://h:1/a")
        self.assertEqual(join("http://h:1", "http://x/y"), "http://x/y")


class ExpandTest(unittest.TestCase):
    def test_data_rows_expand(self):
        case = valid(name="参数化", data=[{"user": "a"}, {"user": "b"}],
                     params={"user": "${user}"})
        rows = case_loader.expand_cases([case])
        self.assertEqual([row["name"] for row in rows], ["参数化[1]", "参数化[2]"])
        self.assertEqual(rows[0]["vars"], {"user": "a"})
        self.assertNotIn("data", rows[0])

    def test_data_and_data_file_conflict(self):
        case = valid(data=[{"a": 1}], data_file="case/data/login.csv")
        with self.assertRaises(CaseError):
            case_loader.expand_cases([case])

    def test_steps_are_not_shared(self):
        # v0.9 的 bug：浅拷贝让参数化各行共享 setup，URL 被拼两遍
        case = valid(name="带前置", data=[{"user": "a"}, {"user": "b"}],
                     params={"user": "${user}"},
                     setup=[{"name": "前置", "url": "/echo", "method": "GET", "params": {}}])
        rows = case_loader.expand_cases([case])
        self.assertIsNot(rows[0]["setup"], rows[1]["setup"])
        prepared = [case_loader.prepare(row, BASE_CONFIG, {}, 5) for row in rows]
        urls = [item["setup"][0]["url"] for item in prepared]
        self.assertEqual(urls, ["http://127.0.0.1:9300/echo"] * 2)

    def test_original_case_not_mutated(self):
        case = valid(name="参数化", data=[{"user": "a"}], params={"user": "${user}"})
        case_loader.expand_cases([case])
        self.assertIn("data", case)
        self.assertEqual(case["name"], "参数化")


class DataFileTest(unittest.TestCase):
    def test_csv_types(self):
        rows = case_loader.read_data_file("case/data/login.csv")
        self.assertEqual(rows[0], {"user": "test001", "pwd": "right123", "want": 0})
        self.assertIsInstance(rows[1]["want"], int)

    def test_json_rows(self):
        rows = case_loader.read_data_file("case/data/login.json")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["want"], 1)

    def test_missing_file(self):
        with self.assertRaises(CaseError):
            case_loader.read_data_file("case/data/nope.csv")

    def test_unsupported_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.txt"
            path.write_text("x", encoding="utf-8")
            with self.assertRaises(CaseError):
                case_loader.read_data_file(str(path))

    def test_coerce(self):
        coerce = case_loader.coerce
        self.assertIs(coerce("true"), True)
        self.assertIs(coerce("False"), False)
        self.assertEqual(coerce("12"), 12)
        self.assertEqual(coerce("1.5"), 1.5)
        self.assertEqual(coerce(" test "), "test")


class OrderTest(unittest.TestCase):
    def test_topological_order(self):
        cases = [{"name": "b", "depends_on": ["a"]}, {"name": "a", "depends_on": []}]
        ordered = case_loader.order_by_deps(cases)
        self.assertEqual([case["name"] for case in ordered], ["a", "b"])

    def test_string_dep_is_normalized(self):
        cases = [{"name": "b", "depends_on": "a"}, {"name": "a"}]
        ordered = case_loader.order_by_deps(cases)
        self.assertEqual(ordered[0]["depends_on"], [])

    def test_duplicate_name(self):
        with self.assertRaises(CaseError) as ctx:
            case_loader.order_by_deps([{"name": "a"}, {"name": "a"}])
        self.assertIn("重复", str(ctx.exception))

    def test_missing_dependency(self):
        with self.assertRaises(CaseError) as ctx:
            case_loader.order_by_deps([{"name": "a", "depends_on": ["nope"]}])
        self.assertIn("不存在", str(ctx.exception))

    def test_cycle(self):
        cases = [{"name": "a", "depends_on": ["b"]}, {"name": "b", "depends_on": ["a"]}]
        with self.assertRaises(CaseError) as ctx:
            case_loader.order_by_deps(cases)
        self.assertIn("成环", str(ctx.exception))


class SerialTest(unittest.TestCase):
    def test_save_and_placeholder_make_serial(self):
        cases = [{"name": "a", "save": {"token": "token"}},
                 {"name": "b", "headers": {"X-Token": "${token}"}},
                 {"name": "c"}]
        case_loader.mark_serial_chain(cases)
        self.assertTrue(cases[0]["serial"])
        self.assertTrue(cases[1]["serial"])
        self.assertFalse(cases[2]["serial"])

    def test_dependency_chain_propagates(self):
        cases = [{"name": "a"}, {"name": "b", "depends_on": ["a"], "serial": True}]
        case_loader.mark_serial_chain(cases)
        self.assertTrue(cases[0]["serial"])
        self.assertIn("依赖链带上", cases[0]["serial_reasons"])

    def test_env_placeholder_does_not_force_serial(self):
        case = {"name": "a", "headers": {"X-Token": "${ENV:HOME}"}}
        self.assertEqual(case_loader.serial_reasons(case), [])

    def test_explicit_serial_reason(self):
        self.assertEqual(case_loader.serial_reasons({"name": "a", "serial": True}),
                         ["显式 serial"])


class ValidateTest(unittest.TestCase):
    def test_valid_case_passes(self):
        warnings = []
        case_loader.validate_case(valid(), "case/x.yaml 第1条", False, warnings)
        self.assertEqual(warnings, [])

    def test_unknown_key_warns_and_strict_raises(self):
        case = valid(expcet=[{"field": "code", "op": "eq", "value": 0}])
        warnings = []
        case_loader.validate_case(case, "case/x.yaml 第1条", False, warnings)
        self.assertEqual(len(warnings), 1)
        self.assertIn("expcet", warnings[0])
        with self.assertRaises(CaseError):
            case_loader.validate_case(case, "case/x.yaml 第1条", True, [])

    def test_unknown_op_has_location(self):
        case = valid(expect=[{"field": "code", "op": "eqq", "value": 0}])
        with self.assertRaises(CaseError) as ctx:
            case_loader.validate_case(case, "case/login.yaml 第2条", False, [])
        message = str(ctx.exception)
        self.assertIn("case/login.yaml 第2条", message)
        self.assertIn("第1条断言", message)
        self.assertIn("eqq", message)

    def test_missing_name(self):
        case = valid()
        del case["name"]
        with self.assertRaises(CaseError):
            case_loader.validate_case(case, "x", False, [])

    def test_bad_url(self):
        with self.assertRaises(CaseError):
            case_loader.validate_case(valid(url="login"), "x", False, [])

    def test_bad_method(self):
        with self.assertRaises(CaseError) as ctx:
            case_loader.validate_case(valid(method="FETCH"), "x", False, [])
        self.assertIn("method", str(ctx.exception))

    def test_empty_expect(self):
        with self.assertRaises(CaseError):
            case_loader.validate_case(valid(expect=[]), "x", False, [])

    def test_timeout_must_be_positive(self):
        with self.assertRaises(CaseError):
            case_loader.validate_case(valid(timeout=0), "x", False, [])

    def test_bad_serial_type(self):
        with self.assertRaises(CaseError):
            case_loader.validate_case(valid(serial="yes"), "x", False, [])

    def test_setup_step_needs_name(self):
        with self.assertRaises(CaseError):
            case_loader.validate_case(valid(setup=[{"url": "/x", "method": "GET"}]),
                                      "x", False, [])

    def test_setup_step_bad_method(self):
        with self.assertRaises(CaseError):
            case_loader.validate_case(valid(setup=[{"name": "s", "url": "/x", "method": "WAT"}]),
                                      "x", False, [])


if __name__ == "__main__":
    unittest.main()
