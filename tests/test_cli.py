"""命令行的单元测试：参数、退出码、--list。"""

import contextlib
import io
import tempfile
import unittest

from tests.helpers import ROOT  # noqa: F401

from engine import cli


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class ParserTest(unittest.TestCase):
    def test_defaults(self):
        args = cli.build_parser().parse_args([])
        self.assertEqual(args.workers, 1)
        self.assertEqual(args.rerun, 0)
        self.assertIsNone(args.tag)
        self.assertFalse(args.list)
        self.assertFalse(args.strict)

    def test_version(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_workers_zero_is_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["--workers", "0"])
        self.assertEqual(ctx.exception.code, cli.EXIT_BAD_INPUT)


class ListTest(unittest.TestCase):
    def test_lists_all_cases_with_lanes(self):
        code, out, _ = run_cli(["--list"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("共23条", out)
        self.assertIn("[串行]", out)
        self.assertIn("[并发]", out)
        self.assertIn("save 变量", out)

    def test_tag_filter(self):
        code, out, _ = run_cli(["--list", "--tag", "smoke"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("共2条", out)

    def test_tag_brings_dependencies(self):
        code, out, _ = run_cli(["--list", "--tag", "depend"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("补上依赖 1 条", out)
        self.assertIn("共2条", out)

    def test_unknown_tag_is_empty(self):
        code, out, _ = run_cli(["--list", "--tag", "没有这个标签"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("共0条", out)

    def test_strict_with_clean_cases(self):
        code, out, err = run_cli(["--list", "--strict"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(err, "")


class ErrorTest(unittest.TestCase):
    def test_unknown_env(self):
        code, _, err = run_cli(["--list", "--env", "nope"])
        self.assertEqual(code, cli.EXIT_BAD_INPUT)
        self.assertIn("找不到环境配置", err)

    def test_load_last_failed_missing(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cli._load_last_failed(Path(tmp) / "nope.json"), [])

    def test_load_last_failed_broken(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "last-failed.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(cli._load_last_failed(path), [])

    def test_keep_zero_is_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["--list", "--keep", "0"])
        self.assertEqual(ctx.exception.code, cli.EXIT_BAD_INPUT)


class WithDepsTest(unittest.TestCase):
    def test_adds_missing_dependency(self):
        all_cases = [{"name": "a"}, {"name": "b", "depends_on": ["a"]}]
        chosen = [all_cases[1]]
        result = cli.with_deps(chosen, all_cases, "测试")
        self.assertEqual([case["name"] for case in result], ["a", "b"])

    def test_keeps_original_order(self):
        all_cases = [{"name": "a"}, {"name": "b", "depends_on": ["a"]},
                     {"name": "c", "depends_on": ["b"]}]
        result = cli.with_deps([all_cases[2]], all_cases, "测试")
        self.assertEqual([case["name"] for case in result], ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
