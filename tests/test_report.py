"""报告层的单元测试：总账、JUnit、result.json、历史、归档、失败清单。"""

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tests.helpers import ROOT

from engine import __version__, report
from engine.result import PASS, FAIL, FLAKY, SKIP, Cause, Outcome

META = {
    "time": "2026-10-22 14:00",
    "stamp": "20261022-140000",
    "env": "default",
    "tag": "全部",
    "workers": 1,
    "base_url": "http://127.0.0.1:9300",
}


def sample_rows():
    return [
        Outcome(name="过", index=1, status=PASS, elapsed_ms=12.0,
                expect_text="code=0", actual_text="code=0", status_code=200),
        Outcome(name="挂", index=2, status=FAIL, cause=Cause.ASSERT,
                reason="code：期望 0，实际 1", elapsed_ms=20.0,
                expect_text="code=0", actual_text="code=1", status_code=200),
        Outcome(name="抖", index=3, status=FLAKY, cause=Cause.TIMEOUT,
                reason="重试 3 次仍未拿到响应", elapsed_ms=35.0),
        Outcome(name="跳", index=4, status=SKIP, cause=Cause.DEPENDENCY,
                reason="依赖 登录 未通过"),
    ]


class SummaryTest(unittest.TestCase):
    def test_counts_and_slowest(self):
        summary = report.summarize(sample_rows(), wall_ms=80.0)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["passed"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["flaky"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["pass_rate"], 25)
        self.assertEqual(summary["slowest"].name, "抖")
        self.assertEqual(summary["case_sum"], 67.0)
        self.assertEqual(summary["retried"], 0)

    def test_empty(self):
        summary = report.summarize([], wall_ms=0)
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["pass_rate"], 0)
        self.assertIsNone(summary["slowest"])


class JunitTest(unittest.TestCase):
    def test_status_mapping(self):
        rows = sample_rows()
        rows[0].retried = 2
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "junit.xml"
            report.write_junit(rows, path, "default", 0.08)
            suite = ET.parse(path).getroot().find("testsuite")
            self.assertEqual(suite.get("tests"), "4")
            self.assertEqual(suite.get("failures"), "1")
            self.assertEqual(suite.get("errors"), "1")
            self.assertEqual(suite.get("skipped"), "1")
            by_name = {case.get("name"): case for case in suite.findall("testcase")}
            self.assertIsNotNone(by_name["挂"].find("failure"))
            self.assertIsNotNone(by_name["抖"].find("error"))
            self.assertIsNotNone(by_name["跳"].find("skipped"))
            self.assertIn("第2轮重跑后通过", by_name["过"].find("system-out").text)
            self.assertIn("断言未通过", by_name["挂"].find("failure").get("message"))


class ResultJsonTest(unittest.TestCase):
    def test_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            report.write_result_json(sample_rows(), report.summarize(sample_rows(), 80.0),
                                     META, path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["meta"]["tool"], "mini-ui-autotest")
            self.assertEqual(payload["meta"]["version"], __version__)
            self.assertEqual(payload["summary"]["total"], 4)
            self.assertEqual(payload["summary"]["slowest"], "抖")
            self.assertEqual(payload["cases"][1]["cause"], "assert")
            self.assertEqual(payload["cases"][1]["cause_label"], "断言未通过")
            self.assertIn("checks", payload["cases"][0])


class HistoryTest(unittest.TestCase):
    def test_trimmed_and_appended(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = Path(tmp)
            history = [{"time": f"2026-10-01 10:{i:02d}", "env": "default", "tag": "全部",
                        "total": 1, "passed": 1, "failed": 0, "flaky": 0,
                        "retried": 0, "wall_ms": 1} for i in range(report.MAX_HISTORY)]
            (report_dir / "history.json").write_text(
                json.dumps(history, ensure_ascii=False), encoding="utf-8")
            report.write_reports(sample_rows(), META, report_dir, ROOT / "templates",
                                 keep=20, wall_ms=80.0)
            saved = json.loads((report_dir / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(len(saved), report.MAX_HISTORY)
            self.assertEqual(saved[-1]["passed"], 1)
            self.assertEqual(saved[-1]["time"], META["time"])

    def test_broken_history_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(report.load_history(path), [])


class ArchiveTest(unittest.TestCase):
    def test_keep_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = Path(tmp)
            for stamp in ("20261022-140001", "20261022-140002", "20261022-140003"):
                meta = dict(META, stamp=stamp)
                report.write_reports(sample_rows(), meta, report_dir,
                                     ROOT / "templates", keep=2, wall_ms=80.0)
            files = sorted((report_dir / "archive").glob("*.html"))
            self.assertEqual(len(files), 2)
            self.assertIn("20261022-140003", files[-1].name)


class ArtifactsTest(unittest.TestCase):
    def test_html_and_last_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = Path(tmp)
            paths = report.write_reports(sample_rows(), META, report_dir,
                                         ROOT / "templates", keep=5, wall_ms=80.0)
            html = paths["html"].read_text(encoding="utf-8")
            self.assertIn("挂", html)
            self.assertIn("断言未通过", html)
            self.assertIn("data-status=\"FAIL\"", html)
            self.assertIn("25%", html)
            for key in ("junit", "json", "archive", "history", "last_failed"):
                self.assertTrue(paths[key].exists(), key)

            failed = json.loads(paths["last_failed"].read_text(encoding="utf-8"))
            self.assertEqual(failed["failed"], ["挂", "抖"])


if __name__ == "__main__":
    unittest.main()
