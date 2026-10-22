"""端到端：在随机端口起一个 demo 服务，真发请求，走完加载 → 调度 → 报告。"""

import importlib.util
import json
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tests.helpers import BASE_CONFIG, ROOT

from engine import case_loader, report, runner
from engine.result import FAIL, PASS, Cause


def load_demo_module():
    path = ROOT / "demo" / "login_server.py"
    spec = importlib.util.spec_from_file_location("demo_login_server_for_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_real_case(base_url, path, expect, **overrides):
    case = {
        "name": path,
        "index": 1,
        "url": base_url + path,
        "method": "GET",
        "params": {},
        "headers": {},
        "timeout": 5,
        "expect": expect,
        "setup": [],
        "teardown": [],
        "depends_on": [],
        "serial": True,
    }
    case.update(overrides)
    return case


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = load_demo_module()
        cls.demo.Handler.log_message = lambda *args: None  # 测试里别刷屏
        cls.server = cls.demo.serve(0)
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def config(self):
        return dict(BASE_CONFIG, base_url=self.base_url, retry_count=1, timeout=5)

    def quiet_options(self):
        return runner.RunOptions(quiet=True, retry_count=1,
                                 sensitive=tuple(self.config()["sensitive"]))

    def run_real(self, case):
        return runner.run_one(case, {}, {}, runner.RunOptions(quiet=True, retry_count=1))

    # ---- 整轮跑

    def test_all_cases_pass_against_demo(self):
        loaded = case_loader.load_cases(config=self.config())
        cases = [case for case in loaded.cases if "slow" not in (case.get("tags") or [])]
        self.assertEqual(len(cases), 21)
        rows = runner.run_all(cases, self.quiet_options())
        broken = [(row.name, row.status, row.reason) for row in rows if row.status != PASS]
        self.assertEqual(broken, [])

    def test_parallel_run_also_passes(self):
        loaded = case_loader.load_cases(config=self.config())
        cases = [case for case in loaded.cases if "slow" not in (case.get("tags") or [])]
        options = runner.RunOptions(quiet=True, retry_count=1, workers=4)
        rows = runner.run_all(cases, options)
        self.assertTrue(all(row.status == PASS for row in rows))

    # ---- 非 JSON 响应的判决（v0.9 的坑）

    def test_non_json_body_fails_with_json_cause(self):
        case = make_real_case(self.base_url, "/html",
                              [{"field": "code", "op": "eq", "value": 0}])
        row = self.run_real(case)
        self.assertEqual(row.status, FAIL)
        self.assertEqual(row.cause, Cause.JSON)
        self.assertIn("不是 JSON", row.reason)

    def test_non_json_body_with_status_only_passes(self):
        case = make_real_case(self.base_url, "/html",
                              [{"field": "status_code", "op": "eq", "value": 200}])
        row = self.run_real(case)
        self.assertEqual(row.status, PASS)
        self.assertIn("不是 JSON", row.reason)

    def test_broken_json_fails_with_json_cause(self):
        case = make_real_case(self.base_url, "/broken",
                              [{"field": "code", "op": "eq", "value": 0}])
        row = self.run_real(case)
        self.assertEqual(row.status, FAIL)
        self.assertEqual(row.cause, Cause.JSON)

    def test_404_json_is_assertable(self):
        case = make_real_case(self.base_url, "/notfound", [
            {"field": "status_code", "op": "eq", "value": 404},
            {"field": "code", "op": "eq", "value": 404},
        ])
        row = self.run_real(case)
        self.assertEqual(row.status, PASS)

    # ---- 产物

    def test_reports_are_written(self):
        loaded = case_loader.load_cases(config=self.config())
        cases = [case for case in loaded.cases if "slow" not in (case.get("tags") or [])]
        rows = runner.run_all(cases, self.quiet_options())
        meta = {
            "time": "2026-10-22 14:00",
            "stamp": "20261022-140000",
            "env": "default",
            "tag": "全部",
            "workers": 1,
            "base_url": self.base_url,
        }
        with tempfile.TemporaryDirectory() as tmp:
            paths = report.write_reports(rows, meta, Path(tmp), ROOT / "templates",
                                         keep=3, wall_ms=1.0)
            html = paths["html"].read_text(encoding="utf-8")
            self.assertIn("测试报告", html)
            self.assertIn("登录成功", html)
            self.assertIn("100%", html)

            suite = ET.parse(paths["junit"]).getroot().find("testsuite")
            self.assertEqual(suite.get("tests"), str(len(rows)))
            self.assertEqual(suite.get("failures"), "0")

            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["total"], len(rows))
            self.assertEqual(payload["summary"]["passed"], len(rows))
            self.assertEqual(payload["meta"]["version"], "1.0.1")
            self.assertTrue(payload["cases"][0]["attempts"])


if __name__ == "__main__":
    unittest.main()
