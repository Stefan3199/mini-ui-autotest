"""v1.0.1 清单里 8 个修复的回归测试（只在验证副本里跑）。"""

import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import BASE_CONFIG, make_case, ok_response, scripted_send

from engine import case_loader, cli, masking, report, runner
from engine.assertion import validate_item
from engine.case_loader import CaseError
from engine.request_sender import TransportError
from engine.result import FAIL, PASS, SKIP, Cause, Outcome


def valid(**overrides):
    case = {"name": "用例", "url": "/login", "method": "POST", "params": {},
            "expect": [{"field": "code", "op": "eq", "value": 0}]}
    case.update(overrides)
    return case


class TimeoutValidationTest(unittest.TestCase):
    def test_list_items_must_be_numbers(self):
        with self.assertRaises(CaseError) as ctx:
            case_loader.validate_case(valid(timeout=[1, "x"]), "x", False, [])
        self.assertIn("每一项", str(ctx.exception))

    def test_list_ok(self):
        case_loader.validate_case(valid(timeout=[1, 2]), "x", False, [])

    def test_config_timeout_list_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "config.yaml").write_text(
                "base_url: http://x\ntimeout: [1, bad]\n", encoding="utf-8")
            with self.assertRaises(CaseError):
                case_loader.load_config(base_dir=tmp)


class SensitiveActualTest(unittest.TestCase):
    def test_actual_is_masked(self):
        send = scripted_send([ok_response({"x_token": "TOK-abc-123"})])
        case = make_case(expect=[{"field": "x_token", "op": "eq", "value": "TOK-abc-123"}])
        row = runner.run_one(case, {}, {}, runner.RunOptions(quiet=True, retry_count=1), send)
        self.assertEqual(row.status, PASS)
        self.assertEqual(row.checks[0].actual, masking.MASK)
        self.assertIn("x_token=***", row.actual_text)


class SubdirectoryCaseTest(unittest.TestCase):
    def test_subdir_loaded(self):
        body = ("- {name: %s, url: /echo, method: GET, params: {}, "
                "expect: [{field: code, op: eq, value: 0}]}\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "case" / "sub").mkdir(parents=True)
            (root / "case" / "a.yaml").write_text(body % "顶层", encoding="utf-8")
            (root / "case" / "sub" / "b.yaml").write_text(body % "子目录", encoding="utf-8")
            loaded = case_loader.load_cases(base_dir=root, config=BASE_CONFIG)
            self.assertEqual(sorted(c["name"] for c in loaded.cases), ["子目录", "顶层"])

    def test_error_message_uses_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "case" / "sub").mkdir(parents=True)
            (root / "case" / "sub" / "bad.yaml").write_text(
                "- {name: 坏, op: nope}\n", encoding="utf-8")
            with self.assertRaises(CaseError) as ctx:
                case_loader.load_cases(base_dir=root, config=BASE_CONFIG)
            self.assertIn("sub/bad.yaml", str(ctx.exception))


class LastFailedTest(unittest.TestCase):
    def test_skipped_recorded(self):
        rows = [Outcome(name="挂", status=FAIL, cause=Cause.ASSERT),
                Outcome(name="跳", status=SKIP, cause=Cause.DEPENDENCY)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "last-failed.json"
            report.write_last_failed(path, rows, {"time": "t", "env": "default"})
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["failed"], ["挂"])
            self.assertEqual(payload["skipped"], ["跳"])

    def test_cli_merges_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "last-failed.json"
            path.write_text('{"failed": ["a"], "skipped": ["b", "a"]}', encoding="utf-8")
            self.assertEqual(cli._load_last_failed(path), ["a", "b"])


class NestedMaskTest(unittest.TestCase):
    def test_list_of_dicts(self):
        masked = masking.mask({"items": [{"token": "x", "user": "u"}, "plain"]})
        self.assertEqual(masked["items"], [{"token": masking.MASK, "user": "u"}, "plain"])

    def test_empty_still_empty(self):
        self.assertEqual(masking.mask(None), {})


class TeardownNotSwallowedTest(unittest.TestCase):
    def test_reason_mentions_teardown(self):
        send = scripted_send([ok_response({"code": 1}), TransportError("connect", "断")])
        case = make_case(teardown=[{"name": "删用户", "url": "/x", "method": "POST"}])
        row = runner.run_one(case, {}, {}, runner.RunOptions(quiet=True, retry_count=1), send)
        self.assertEqual(row.cause, Cause.ASSERT)
        self.assertIn("teardown 也失败", row.reason)


class RegexValidationTest(unittest.TestCase):
    def test_bad_regex(self):
        problem = validate_item({"field": "name", "op": "regex", "value": "("})
        self.assertIn("正则写错了", problem)

    def test_good_regex(self):
        self.assertIsNone(validate_item(
            {"field": "name", "op": "regex", "value": r"^test\d+$"}))


class ArchiveCollisionTest(unittest.TestCase):
    def test_same_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = {"stamp": "20261022-140000", "tag": "全部", "env": "default"}
            first = report.archive(tmp, "one", meta, keep=20)
            second = report.archive(tmp, "two", meta, keep=20)
            self.assertNotEqual(first, second)
            files = list((Path(tmp) / "archive").glob("*.html"))
            self.assertEqual(len(files), 2)
            self.assertEqual(second.read_text(encoding="utf-8"), "two")


class MoreMaskingTest(unittest.TestCase):
    def test_expect_and_check_are_masked(self):
        send = scripted_send([ok_response({"x_token": "TOK"})])
        case = make_case(expect=[{"field": "x_token", "op": "eq", "value": "TOK"}])
        row = runner.run_one(case, {}, {}, runner.RunOptions(quiet=True, retry_count=1), send)
        self.assertEqual(row.expect_text, "x_token=***")
        self.assertEqual(row.checks[0].expect, "***")

    def test_not_null_expect_text_kept(self):
        send = scripted_send([ok_response({"token": "TOK"})])
        case = make_case(expect=[{"field": "token", "op": "not_null"}])
        row = runner.run_one(case, {}, {}, runner.RunOptions(quiet=True, retry_count=1), send)
        self.assertEqual(row.checks[0].expect, "非空")
        self.assertEqual(row.checks[0].actual, "***")

    def test_saved_value_masked_but_context_keeps_it(self):
        send = scripted_send([ok_response({"code": 0, "token": "TOK"})])
        context = {}
        case = make_case(save={"token": "token"})
        row = runner.run_one(case, context, {}, runner.RunOptions(quiet=True, retry_count=1), send)
        self.assertEqual(row.saved["token"], "***")
        self.assertEqual(context["token"], "TOK")


if __name__ == "__main__":
    unittest.main()
