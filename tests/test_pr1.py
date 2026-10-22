"""PR1（v1.1）回归：CLI 覆盖与重试退避。"""

import contextlib
import io
import tempfile
import time
import unittest
from pathlib import Path

from tests.helpers import make_case, ok_response, scripted_send

from engine import case_loader, cli, runner
from engine.case_loader import CaseError
from engine.request_sender import Response, TransportError
from engine.result import FLAKY, PASS


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class OverridesTest(unittest.TestCase):
    def test_overrides_beat_env_file(self):
        config = case_loader.load_config("dev", overrides={
            "base_url": "http://127.0.0.1:9999",
            "report_dir": "report-ci",
            "retry_count": 1,
            "timeout": 0.5,
        })
        self.assertEqual(config["base_url"], "http://127.0.0.1:9999")
        self.assertEqual(config["report_dir"], "report-ci")
        self.assertEqual(config["retry_count"], 1)
        self.assertEqual(config["timeout"], 0.5)

    def test_env_still_wins_without_overrides(self):
        config = case_loader.load_config("dev")
        self.assertEqual(config["report_dir"], "report-dev")

    def test_retry_backoff_defaults_to_zero(self):
        self.assertEqual(case_loader.load_config()["retry_backoff"], 0)

    def test_retry_backoff_list_allowed(self):
        config = case_loader.load_config(overrides={"retry_backoff": [0.1, 0.2]})
        self.assertEqual(config["retry_backoff"], [0.1, 0.2])

    def test_retry_backoff_negative_rejected(self):
        with self.assertRaises(CaseError):
            case_loader.load_config(overrides={"retry_backoff": -1})

    def test_retry_backoff_string_rejected(self):
        with self.assertRaises(CaseError):
            case_loader.load_config(overrides={"retry_backoff": "fast"})


class CliArgumentTest(unittest.TestCase):
    def test_new_flags_are_parsed(self):
        args = cli.build_parser().parse_args(
            ["--base-url", "http://x", "--report-dir", "r", "--retry", "2", "--timeout", "0.5"])
        self.assertEqual(args.base_url, "http://x")
        self.assertEqual(args.report_dir, "r")
        self.assertEqual(args.retry, 2)
        self.assertEqual(args.timeout, 0.5)

    def test_bad_base_url_is_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["--list", "--base-url", "127.0.0.1:9999"])
        self.assertEqual(ctx.exception.code, cli.EXIT_BAD_INPUT)

    def test_retry_zero_is_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["--list", "--retry", "0"])
        self.assertEqual(ctx.exception.code, cli.EXIT_BAD_INPUT)

    def test_timeout_zero_is_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["--list", "--timeout", "0"])
        self.assertEqual(ctx.exception.code, cli.EXIT_BAD_INPUT)

    def test_overrides_do_not_break_list(self):
        code, out, _ = run_cli(["--list", "--base-url", "http://127.0.0.1:9999",
                                "--report-dir", "report-ci", "--retry", "1",
                                "--timeout", "0.5"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("共23条", out)


class BackoffTest(unittest.TestCase):
    def options(self, **overrides):
        calls = []
        options = runner.RunOptions(quiet=True, sleep=calls.append, **overrides)
        return options, calls

    def test_sleeps_between_attempts(self):
        options, calls = self.options(retry_count=3, retry_backoff=0.5)
        send = scripted_send([TransportError("connect", "断")])
        row = runner.run_one(make_case(), {}, {}, options, send)
        self.assertEqual(row.status, FLAKY)
        self.assertEqual(calls, [0.5, 0.5])
        self.assertEqual([item.waited_ms for item in row.attempts], [0, 500, 500])

    def test_no_sleep_when_first_try_succeeds(self):
        options, calls = self.options(retry_count=3, retry_backoff=0.5)
        send = scripted_send([ok_response({"code": 0})])
        row = runner.run_one(make_case(), {}, {}, options, send)
        self.assertEqual(row.status, PASS)
        self.assertEqual(calls, [])

    def test_no_sleep_after_last_attempt(self):
        options, calls = self.options(retry_count=2, retry_backoff=1)
        send = scripted_send([TransportError("connect", "断")])
        runner.run_one(make_case(), {}, {}, options, send)
        self.assertEqual(calls, [1.0])

    def test_list_backoff_grows_then_repeats_last(self):
        options, calls = self.options(retry_count=4, retry_backoff=[0.1, 0.2])
        send = scripted_send([TransportError("timeout", "慢")])
        runner.run_one(make_case(), {}, {}, options, send)
        self.assertEqual(calls, [0.1, 0.2, 0.2])

    def test_protocol_error_does_not_wait(self):
        options, calls = self.options(retry_count=3, retry_backoff=0.5)
        send = scripted_send([Response(200, None, text="<html>", body_kind="text")])
        row = runner.run_one(make_case(), {}, {}, options, send)
        self.assertEqual(calls, [])
        self.assertEqual(row.attempts[0].waited_ms, 0)

    def test_backoff_delay_helper(self):
        self.assertEqual(runner.backoff_delay(0, 1), 0.0)
        self.assertEqual(runner.backoff_delay(0.5, 3), 0.5)
        self.assertEqual(runner.backoff_delay([1, 2], 1), 1.0)
        self.assertEqual(runner.backoff_delay([1, 2], 5), 2.0)
        self.assertEqual(runner.backoff_delay([], 2), 0.0)

    def test_attempts_serialize_waited_ms(self):
        options, _ = self.options(retry_count=2, retry_backoff=0.2)
        send = scripted_send([TransportError("connect", "断")])
        row = runner.run_one(make_case(), {}, {}, options, send)
        payload = row.to_dict()
        self.assertEqual([item["waited_ms"] for item in payload["attempts"]], [0, 200])


class EndToEndOverrideTest(unittest.TestCase):
    """三个覆盖参数一起用：连不上的地址 + 只试 1 次 + 0.5 秒超时 + 临时报告目录。"""

    def test_dead_target_is_fast_and_flaky(self):
        with tempfile.TemporaryDirectory() as tmp:
            started = time.perf_counter()
            code, _, _ = run_cli(["--tag", "smoke",
                                  "--base-url", "http://127.0.0.1:1",
                                  "--retry", "1", "--timeout", "0.5",
                                  "--report-dir", tmp, "--quiet"])
            elapsed = time.perf_counter() - started
            self.assertEqual(code, cli.EXIT_FLAKY)
            self.assertTrue((Path(tmp) / "result.json").exists())
            self.assertLess(elapsed, 10, f"覆盖参数没生效，跑了 {elapsed:.1f}s")


if __name__ == "__main__":
    unittest.main()
