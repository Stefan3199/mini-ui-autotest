"""v1.1 PR2：fixture 体系的回归测试。

覆盖三块：文件校验、作用域语义（session 一次 / case 每条）、失败判 SKIP。
"""

import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.helpers import make_case, ok_response

from engine import fixtures, runner
from engine.request_sender import TransportError
from engine.result import PASS, SKIP, Cause


def load(text):
    """把 YAML 文本写进临时文件再加载，顺带测到文件校验。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fixtures.yaml"
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return fixtures.load_fixtures(
            path, {"base_url": "http://demo"}, {"X-Token": "default"}, 5)


def options(**overrides):
    return runner.RunOptions(quiet=True, retry_count=1, **overrides)


def make_manager(defs, send, **overrides):
    return fixtures.FixtureManager(defs, options(**overrides), send)


def route_send(token="TOK-1"):
    """按 URL 分流：/login 发 token，其他把请求头原样回显。"""
    calls = []

    def send(case):
        calls.append(case)
        if case["url"].endswith("/login"):
            return ok_response({"code": 0, "token": token})
        return ok_response({"code": 0, "x_token": case["headers"].get("X-Token", "")})

    send.calls = calls
    return send


LOGIN = """
fixtures:
  login:
    scope: session
    request:
      url: /login
      method: POST
    save:
      token: token
"""


class LoadTest(unittest.TestCase):
    def test_missing_file_is_empty(self):
        self.assertEqual(fixtures.load_fixtures("/tmp/没有这个文件.yaml"), {})

    def test_reads_scope_and_save(self):
        defs = load(LOGIN)
        self.assertEqual(defs["login"].scope, "session")
        self.assertEqual(defs["login"].save, {"token": "token"})
        self.assertEqual(defs["login"].request["url"], "http://demo/login")
        self.assertEqual(defs["login"].request["method"], "POST")

    def test_scope_defaults_to_case(self):
        defs = load("""
            fixtures:
              thing:
                request: {url: /x}
        """)
        self.assertEqual(defs["thing"].scope, "case")

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(fixtures.FixtureError):
            load("""
                fixtures:
                  thing:
                    request: {url: /x}
                    retries: 3
            """)

    def test_bad_scope_is_rejected(self):
        with self.assertRaises(fixtures.FixtureError):
            load("""
                fixtures:
                  thing:
                    scope: global
                    request: {url: /x}
            """)

    def test_missing_dependency_is_rejected(self):
        with self.assertRaises(fixtures.FixtureError) as ctx:
            load("""
                fixtures:
                  thing:
                    request: {url: /x}
                    depends_on: [nope]
            """)
        self.assertIn("不存在", str(ctx.exception))

    def test_cycle_is_rejected(self):
        with self.assertRaises(fixtures.FixtureError) as ctx:
            load("""
                fixtures:
                  a:
                    request: {url: /a}
                    depends_on: [b]
                  b:
                    request: {url: /b}
                    depends_on: [a]
            """)
        self.assertIn("成环", str(ctx.exception))

    def test_session_cannot_depend_on_case(self):
        with self.assertRaises(fixtures.FixtureError):
            load("""
                fixtures:
                  a:
                    scope: session
                    request: {url: /a}
                    depends_on: [b]
                  b:
                    request: {url: /b}
            """)

    def test_order_puts_dependency_first(self):
        defs = load("""
            fixtures:
              a:
                request: {url: /a}
              b:
                request: {url: /b}
                depends_on: [a]
        """)
        self.assertEqual(fixtures.order(["b"], defs), ["a", "b"])

    def test_bad_expect_is_rejected(self):
        with self.assertRaises(fixtures.FixtureError):
            load("""
                fixtures:
                  thing:
                    request: {url: /x}
                    expect:
                      - {field: code, op: 不存在的算子, value: 0}
            """)


class SessionScopeTest(unittest.TestCase):
    def test_runs_once_for_many_cases(self):
        defs = load(LOGIN)
        send = route_send()
        manager = make_manager(defs, send)
        cases = [
            make_case(name="a", url="http://demo/echo", fixtures=["login"],
                      headers={"X-Token": "${token}"},
                      expect=[{"field": "x_token", "op": "eq", "value": "TOK-1"}]),
            make_case(name="b", url="http://demo/echo", fixtures=["login"],
                      headers={"X-Token": "${token}"},
                      expect=[{"field": "x_token", "op": "eq", "value": "TOK-1"}]),
        ]
        rows = runner.run_all(cases, options(), send=send, fixtures=manager)
        self.assertEqual([row.status for row in rows], [PASS, PASS])
        logins = [call for call in send.calls if call["url"].endswith("/login")]
        self.assertEqual(len(logins), 1)

    def test_value_reaches_parallel_lane(self):
        defs = load(LOGIN)
        send = route_send()
        manager = make_manager(defs, send, workers=2)
        cases = [
            make_case(name=f"c{i}", url="http://demo/echo", fixtures=["login"],
                      headers={"X-Token": "${token}"},
                      expect=[{"field": "x_token", "op": "eq", "value": "TOK-1"}])
            for i in range(4)
        ]
        rows = runner.run_all(cases, options(workers=2), send=send, fixtures=manager)
        self.assertTrue(all(row.status == PASS for row in rows))
        logins = [call for call in send.calls if call["url"].endswith("/login")]
        self.assertEqual(len(logins), 1)

    def test_failure_skips_cases(self):
        defs = load(LOGIN)
        send = route_send()
        send = _fail_login(send)
        manager = make_manager(defs, send)
        cases = [make_case(name="a", url="http://demo/echo", fixtures=["login"])]
        rows = runner.run_all(cases, options(), send=send, fixtures=manager)
        self.assertEqual(rows[0].status, SKIP)
        self.assertEqual(rows[0].cause, Cause.FIXTURE)
        self.assertIn("login", manager.failures)

    def test_teardown_runs_once(self):
        defs = load("""
            fixtures:
              login:
                scope: session
                request: {url: /login, method: POST}
                save: {token: token}
                teardown: {url: /logout, method: POST}
        """)
        send = route_send()
        manager = make_manager(defs, send)
        cases = [make_case(name="a", url="http://demo/echo", fixtures=["login"],
                           headers={"X-Token": "${token}"},
                           expect=[{"field": "x_token", "op": "eq", "value": "TOK-1"}])]
        runner.run_all(cases, options(), send=send, fixtures=manager)
        logouts = [call for call in send.calls if call["url"].endswith("/logout")]
        self.assertEqual(len(logouts), 1)


class CaseScopeTest(unittest.TestCase):
    CASE_FIXTURE = """
        fixtures:
          temp:
            scope: case
            request: {url: /admin/user, method: POST}
            save: {exists: exists}
            teardown: {url: /admin/user/delete, method: POST}
    """

    def test_runs_for_each_case(self):
        defs = load(self.CASE_FIXTURE)
        send = _temp_send()
        manager = make_manager(defs, send)
        cases = [
            make_case(name="a", url="http://demo/admin/user/get", fixtures=["temp"],
                      expect=[{"field": "exists", "op": "eq", "value": True}]),
            make_case(name="b", url="http://demo/admin/user/get", fixtures=["temp"],
                      expect=[{"field": "exists", "op": "eq", "value": True}]),
        ]
        rows = runner.run_all(cases, options(), send=send, fixtures=manager)
        self.assertEqual([row.status for row in rows], [PASS, PASS])
        creates = [call for call in send.calls if call["url"].endswith("/admin/user")]
        deletes = [call for call in send.calls if call["url"].endswith("/admin/user/delete")]
        self.assertEqual(len(creates), 2)
        self.assertEqual(len(deletes), 2)

    def test_teardown_runs_after_case(self):
        defs = load(self.CASE_FIXTURE)
        order = []

        def send(case):
            if case["url"].endswith("/admin/user"):
                order.append("create")
                return ok_response({"code": 0, "exists": True})
            if case["url"].endswith("/admin/user/delete"):
                order.append("delete")
                return ok_response({"code": 0})
            order.append("case")
            return ok_response({"code": 0, "exists": True})

        manager = make_manager(defs, send)
        cases = [make_case(name="a", url="http://demo/admin/user/get", fixtures=["temp"],
                           expect=[{"field": "exists", "op": "eq", "value": True}])]
        runner.run_all(cases, options(), send=send, fixtures=manager)
        self.assertEqual(order, ["create", "case", "delete"])

    def test_fixture_expect_failure_skips(self):
        defs = load("""
            fixtures:
              bad:
                request: {url: /login, method: POST}
                save: {token: token}
                expect:
                  - {field: token, op: not_null}
        """)
        send = lambda case: ok_response({"code": 0, "token": None})
        manager = make_manager(defs, send)
        cases = [make_case(name="a", url="http://demo/echo", fixtures=["bad"])]
        rows = runner.run_all(cases, options(), send=send, fixtures=manager)
        self.assertEqual(rows[0].status, SKIP)
        self.assertIn("bad", manager.failures)


class WithoutManagerTest(unittest.TestCase):
    def test_case_with_fixture_without_manager_is_skipped(self):
        case = make_case(fixtures=["login"])
        row = runner.run_one(case, {}, {}, options(),
                             send=lambda item: ok_response())
        self.assertEqual(row.status, SKIP)
        self.assertEqual(row.cause, Cause.FIXTURE)
        self.assertIn("没有加载", row.reason)


def _fail_login(send):
    def broken(case):
        if case["url"].endswith("/login"):
            raise TransportError("connect", "连不上")
        return send(case)

    return broken


def _temp_send():
    calls = []

    def send(case):
        calls.append(case)
        if case["url"].endswith("/admin/user"):
            return ok_response({"code": 0, "exists": True})
        if case["url"].endswith("/admin/user/delete"):
            return ok_response({"code": 0})
        return ok_response({"code": 0, "exists": True})

    send.calls = calls
    return send


if __name__ == "__main__":
    unittest.main()
