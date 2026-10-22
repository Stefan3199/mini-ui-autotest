"""调度层的单元测试，用假 sender，不出网。"""

import threading
import time
import unittest

from tests.helpers import make_case, ok_response, scripted_send

from engine import runner
from engine.request_sender import Response, TransportError
from engine.result import FAIL, FLAKY, PASS, SKIP, Cause, Outcome


def opts(**overrides):
    options = {"quiet": True, "retry_count": 1}
    options.update(overrides)
    return runner.RunOptions(**options)


class SingleCaseTest(unittest.TestCase):
    def test_pass(self):
        send = scripted_send([ok_response({"code": 0, "token": "T"})])
        row = runner.run_one(make_case(), {}, {}, opts(), send)
        self.assertEqual(row.status, PASS)
        self.assertEqual(row.cause, "")
        self.assertEqual(row.status_code, 200)
        self.assertTrue(all(check.ok for check in row.checks))

    def test_assert_fail_is_not_retried(self):
        send = scripted_send([ok_response({"code": 1})])
        row = runner.run_one(make_case(), {}, {}, opts(retry_count=3), send)
        self.assertEqual(row.status, FAIL)
        self.assertEqual(row.cause, Cause.ASSERT)
        self.assertEqual(send.record["count"], 1)
        self.assertIn("期望 0", row.reason)

    def test_transport_errors_retry_then_flaky(self):
        send = scripted_send([TransportError("timeout", "慢")])
        row = runner.run_one(make_case(), {}, {}, opts(retry_count=3), send)
        self.assertEqual(row.status, FLAKY)
        self.assertEqual(row.cause, Cause.TIMEOUT)
        self.assertEqual(send.record["count"], 3)
        self.assertEqual(len(row.attempts), 3)
        self.assertIn("重试 3 次", row.reason)

    def test_transport_error_recovers(self):
        send = scripted_send([TransportError("connect", "断"),
                              TransportError("connect", "断"),
                              ok_response({"code": 0})])
        row = runner.run_one(make_case(), {}, {}, opts(retry_count=3), send)
        self.assertEqual(row.status, PASS)
        self.assertEqual(len(row.attempts), 3)
        self.assertEqual([item.ok for item in row.attempts], [False, False, True])

    def test_protocol_error_with_status_only_still_passes(self):
        # 非 JSON 响应 + 只断状态码：以前这条永远拿不到响应，现在要能过
        send = scripted_send([Response(404, None, text="", body_kind="text")])
        case = make_case(expect=[{"field": "status_code", "op": "eq", "value": 404}])
        row = runner.run_one(case, {}, {}, opts(), send)
        self.assertEqual(row.status, PASS)
        self.assertIn("不是 JSON", row.reason)

    def test_protocol_error_on_body_field_fails_with_json_cause(self):
        send = scripted_send([Response(200, None, text="<html>502</html>", body_kind="text")])
        row = runner.run_one(make_case(), {}, {}, opts(), send)
        self.assertEqual(row.status, FAIL)
        self.assertEqual(row.cause, Cause.JSON)
        self.assertIn("502", row.reason)

    def test_missing_var(self):
        send = scripted_send([ok_response()])
        case = make_case(params={"user": "${nope}"})
        row = runner.run_one(case, {}, {}, opts(), send)
        self.assertEqual(row.status, FAIL)
        self.assertEqual(row.cause, Cause.VAR)
        self.assertEqual(send.record["count"], 0)

    def test_masking_applied_to_outcome(self):
        send = scripted_send([ok_response()])
        case = make_case(params={"user": "u", "pwd": "right123"},
                         headers={"X-Token": "secret"})
        row = runner.run_one(case, {}, {}, opts(), send)
        self.assertEqual(row.params["pwd"], "***")
        self.assertEqual(row.headers["X-Token"], "***")
        self.assertEqual(row.params["user"], "u")

    def test_expect_text_uses_resolved_variables(self):
        send = scripted_send([ok_response({"code": 0})])
        case = make_case(vars={"want": 0},
                         expect=[{"field": "code", "op": "eq", "value": "${want}"}])
        row = runner.run_one(case, {}, {}, opts(), send)
        self.assertEqual(row.expect_text, "code=0")


class DependencyTest(unittest.TestCase):
    def test_failed_dependency_skips(self):
        send = scripted_send([ok_response()])
        case = make_case(depends_on=["登录"])
        row = runner.run_one(case, {}, {"登录": Outcome(name="登录", status=FAIL)}, opts(), send)
        self.assertEqual(row.status, SKIP)
        self.assertEqual(row.cause, Cause.DEPENDENCY)
        self.assertEqual(send.record["count"], 0)

    def test_missing_dependency_skips(self):
        send = scripted_send([ok_response()])
        row = runner.run_one(make_case(depends_on=["不存在"]), {}, {}, opts(), send)
        self.assertEqual(row.status, SKIP)

    def test_passed_dependency_runs(self):
        send = scripted_send([ok_response({"code": 0})])
        case = make_case(depends_on=["登录"])
        row = runner.run_one(case, {}, {"登录": Outcome(name="登录", status=PASS)}, opts(), send)
        self.assertEqual(row.status, PASS)


class SetupTeardownTest(unittest.TestCase):
    def test_setup_failure_skips_main_request(self):
        send = scripted_send([TransportError("connect", "断")])
        case = make_case(setup=[{"name": "建用户", "url": "/x", "method": "POST"}])
        row = runner.run_one(case, {}, {}, opts(), send)
        self.assertEqual(row.status, FAIL)
        self.assertEqual(row.cause, Cause.SETUP)
        self.assertEqual(send.record["count"], 1)
        self.assertIn("建用户", row.reason)

    def test_teardown_runs_even_when_assert_fails(self):
        send = scripted_send([ok_response({"code": 1}), ok_response()])
        case = make_case(teardown=[{"name": "删用户", "url": "/x", "method": "POST"}])
        row = runner.run_one(case, {}, {}, opts(), send)
        self.assertEqual(row.status, FAIL)
        self.assertEqual(row.cause, Cause.ASSERT)
        self.assertEqual(send.record["count"], 2)
        self.assertEqual(row.attempts[-1].phase, "teardown:删用户")

    def test_teardown_failure_fails_case(self):
        send = scripted_send([ok_response({"code": 0}), TransportError("connect", "断")])
        case = make_case(teardown=[{"name": "删用户", "url": "/x", "method": "POST"}])
        row = runner.run_one(case, {}, {}, opts(), send)
        self.assertEqual(row.status, FAIL)
        self.assertEqual(row.cause, Cause.TEARDOWN)


class SaveTest(unittest.TestCase):
    def test_save_only_when_pass(self):
        send = scripted_send([ok_response({"code": 1, "token": "T"})])
        context = {}
        case = make_case(save={"token": "token"})
        runner.run_one(case, context, {}, opts(), send)
        self.assertEqual(context, {})

    def test_save_writes_context(self):
        send = scripted_send([ok_response({"code": 0, "token": "TOK"})])
        context = {}
        case = make_case(save={"token": "token"})
        row = runner.run_one(case, context, {}, opts(), send)
        self.assertEqual(row.status, PASS)
        self.assertEqual(context["token"], "TOK")
        self.assertEqual(row.saved, {"token": "TOK"})

    def test_serial_lane_shares_token(self):
        seen = []

        def send(case):
            seen.append(case["headers"].get("X-Token"))
            return ok_response({"code": 0, "token": "TOK"})

        login = make_case(name="登录", save={"token": "token"})
        use = make_case(name="用 token", headers={"X-Token": "${token}"})
        rows = runner.run_serial([login, use], opts(), send)
        self.assertEqual([row.status for row in rows], [PASS, PASS])
        self.assertEqual(seen, [None, "TOK"])


class LaneTest(unittest.TestCase):
    def test_group_is_mutually_exclusive(self):
        state = {"now": 0, "max": 0}
        lock = threading.Lock()

        def send(case):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            time.sleep(0.05)
            with lock:
                state["now"] -= 1
            return ok_response({"code": 0})

        cases = [make_case(name=f"组内{i}", index=i + 1, group="建用户") for i in range(4)]
        runner.run_parallel(cases, opts(workers=4), send)
        self.assertEqual(state["max"], 1)

    def test_no_group_runs_in_parallel(self):
        state = {"now": 0, "max": 0}
        lock = threading.Lock()

        def send(case):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            time.sleep(0.05)
            with lock:
                state["now"] -= 1
            return ok_response({"code": 0})

        cases = [make_case(name=f"独立{i}", index=i + 1) for i in range(4)]
        runner.run_parallel(cases, opts(workers=4), send)
        self.assertGreaterEqual(state["max"], 2)


class RunAllTest(unittest.TestCase):
    def test_rerun_adopts_pass(self):
        # 第一次断言不中（FAIL），重跑那轮断言命中，记成「第1轮重跑后通过」
        send = scripted_send([ok_response({"code": 1}), ok_response({"code": 0})])
        rows = runner.run_all([make_case()], opts(retry_count=1, rerun=1), send)
        self.assertEqual(rows[0].status, PASS)
        self.assertEqual(rows[0].retried, 1)

    def test_rerun_does_not_retry_flaky(self):
        send = scripted_send([TransportError("timeout", "慢")])
        rows = runner.run_all([make_case()], opts(retry_count=1, rerun=2), send)
        self.assertEqual(rows[0].status, FLAKY)
        self.assertEqual(rows[0].retried, 0)

    def test_rows_sorted_by_index(self):
        send = scripted_send([ok_response({"code": 0})])
        cases = [make_case(name="后", index=3), make_case(name="前", index=1)]
        rows = runner.run_all(cases, opts(), send)
        self.assertEqual([row.index for row in rows], [1, 3])


if __name__ == "__main__":
    unittest.main()
