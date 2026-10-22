"""调度：跑单条用例、分串行/并发两条通道、重跑失败用例。

判决规则集中在这里，别处不再猜：
- 传输错误重试用完 → FLAKY（环境问题）
- 有响应但 body 不是 JSON → body 当空，只涉及状态码的断言照样能过；
  一旦断言碰到响应体字段，判 FAIL，原因记 json
- 断言没中 → FAIL（业务问题），不重试
- 依赖没过 → SKIP，不发请求
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock

from . import assertion, masking, request_sender, variables
from .request_sender import ProtocolError, Response, TransportError
from .result import (FAIL, FLAKY, PASS, SKIP, Attempt, Cause, Outcome,
                     cause_label)


@dataclass
class RunOptions:
    """一轮运行的参数。emit 收日志，默认 print。"""

    workers: int = 1
    rerun: int = 0
    retry_count: int = 3
    quiet: bool = False
    verbose: bool = False
    sensitive: tuple = ()
    emit: object = None
    _lock: Lock = field(default_factory=Lock, repr=False)

    def say(self, message):
        if self.quiet:
            return
        with self._lock:
            (self.emit or print)(message)


def _is_secret_field(field):
    """字段名最后一段命中敏感词就当作秘密，报告里换成 ***。"""
    return masking.is_sensitive(str(field).split(".")[-1])


def _now_ms(start):
    return round((time.perf_counter() - start) * 1000, 1)


def _cause_of(error):
    kind = getattr(error, "kind", "")
    if kind == "timeout":
        return Cause.TIMEOUT
    if kind == "connect":
        return Cause.CONNECT
    return Cause.OTHER


def _error_text(error):
    if error is None:
        return "没拿到响应"
    label = cause_label(_cause_of(error))
    message = getattr(error, "message", str(error))
    return f"{label}：{message}" if label else message


def attempt_request(request, options, send, phase="main"):
    """发一条请求，按 retry_count 重试传输错误。

    返回 (response, attempts, error)：
    - response 可能非空但 error 非空（协议错误：拿到了响应，body 不是 JSON）
    - 传输错误重试用完时 response 为 None
    """
    attempts = []
    last_error = None
    for number in range(1, options.retry_count + 1):
        start = time.perf_counter()
        try:
            response = send(request)
            attempts.append(Attempt(number, True, phase=phase,
                                    status_code=response.status_code,
                                    elapsed_ms=_now_ms(start)))
            return response, attempts, None
        except TransportError as error:
            attempts.append(Attempt(number, False, cause=error.kind, message=error.message,
                                    elapsed_ms=_now_ms(start), phase=phase))
            last_error = error
        except ProtocolError as error:
            elapsed = _now_ms(start)
            attempts.append(Attempt(number, False, cause=error.kind, message=error.message,
                                    status_code=error.status_code, elapsed_ms=elapsed,
                                    phase=phase))
            # 协议错误不重试，但状态码和响应文本要留住：只断状态码的用例还能通过
            response = Response(status_code=error.status_code, body=None, text=error.text,
                                elapsed_ms=elapsed, body_kind="text")
            return response, attempts, error
    return None, attempts, last_error


def _finish(outcome, start, options, status, cause="", reason=""):
    outcome.status = status
    outcome.cause = cause if status != PASS else ""
    outcome.reason = reason
    outcome.elapsed_ms = _now_ms(start)
    if status == PASS:
        line = f"{outcome.name} PASS"
    else:
        label = cause_label(outcome.cause)
        line = f"{outcome.name} {status}" + (f" [{label}]" if label else "") + (f" {reason}" if reason else "")
    options.say(line)
    if options.verbose:
        for item in outcome.attempts:
            options.say(f"    #{item.attempt} {item.phase} "
                        f"{'ok' if item.ok else item.cause} {item.message}".rstrip())
    return outcome


def run_one(case, context=None, done=None, options=None, send=request_sender.send_case):
    """跑一条用例：查依赖、替换变量、跑 setup、发主请求、跑 teardown、判三态。"""
    options = options or RunOptions()
    context = {} if context is None else context
    done = {} if done is None else done

    outcome = Outcome(
        name=case["name"],
        index=case.get("index", 0),
        method=case.get("method", ""),
        url=case.get("url", ""),
        lane="串行" if case.get("serial") else "并发",
        group=case.get("group") or "",
    )
    outcome.params = masking.mask(case.get("params"), options.sensitive)
    outcome.headers = masking.mask(case.get("headers"), options.sensitive)
    outcome.expect_text = assertion.describe_expect(case["expect"], _is_secret_field)
    start = time.perf_counter()

    for dep in case.get("depends_on") or []:
        row = done.get(dep)
        if row is None or row.status != PASS:
            return _finish(outcome, start, options, SKIP, Cause.DEPENDENCY, f"依赖 {dep} 未通过")

    try:
        case = variables.render(case, {**context, **(case.get("vars") or {})})
    except variables.MissingVar as error:
        return _finish(outcome, start, options, FAIL, Cause.VAR,
                       f"变量 ${{{error.name}}} 没定义")

    # 变量替换之后再生成「预期」摘要，否则参数化用例的预期列会显示 ${want} 这种模板
    outcome.expect_text = assertion.describe_expect(case["expect"], _is_secret_field)

    for step in case.get("setup") or []:
        response, attempts, error = attempt_request(step, options, send, phase=f'setup:{step["name"]}')
        outcome.attempts.extend(attempts)
        if response is None:
            return _finish(outcome, start, options, FAIL, Cause.SETUP,
                           f'setup 失败：{step["name"]}（{_error_text(error)}）')

    response, attempts, error = attempt_request(case, options, send, phase="main")
    outcome.attempts.extend(attempts)

    teardown_failed = None
    for step in case.get("teardown") or []:
        _, step_attempts, step_error = attempt_request(step, options, send,
                                                       phase=f'teardown:{step["name"]}')
        outcome.attempts.extend(step_attempts)
        if step_error is not None and teardown_failed is None:
            teardown_failed = f'{step["name"]}（{_error_text(step_error)}）'

    if response is None:
        return _finish(outcome, start, options, FLAKY, _cause_of(error),
                       f"重试 {options.retry_count} 次仍未拿到响应：{_error_text(error)}")

    outcome.status_code = response.status_code
    outcome.body_kind = response.body_kind
    outcome.checks = assertion.run_checks(response.status_code, response.body, case["expect"])
    for check in outcome.checks:
        if not masking.is_sensitive(check.field.split(".")[-1]):
            continue
        check.actual = masking.MASK
        if assertion.OPS[check.op].needs_value:
            check.expect = masking.MASK
    outcome.actual_text = assertion.describe_actual(outcome.checks)

    bad = next((check for check in outcome.checks if not check.ok), None)
    if bad is not None:
        teardown_note = f"；另外 teardown 也失败：{teardown_failed}" if teardown_failed else ""
        if response.body_kind == "text" and bad.field != assertion.STATUS_FIELD:
            return _finish(outcome, start, options, FAIL, Cause.JSON,
                           f"响应不是 JSON，断不了 {bad.field}：{response.text[:80]}{teardown_note}")
        return _finish(outcome, start, options, FAIL, Cause.ASSERT,
                       assertion.describe_bad(bad) + teardown_note)
    if teardown_failed is not None:
        return _finish(outcome, start, options, FAIL, Cause.TEARDOWN,
                       f"teardown 失败：{teardown_failed}")

    if case.get("save"):
        for name, field_path in case["save"].items():
            value = assertion.pick(response.body, field_path)
            context[name] = value
            outcome.saved[name] = masking.MASK if masking.is_sensitive(name) else value

    note = ""
    if response.body_kind == "text":
        note = "响应不是 JSON，只有状态码断言"
    return _finish(outcome, start, options, PASS, reason=note)


def run_serial(cases, options, send=request_sender.send_case):
    """串行通道：一条跑完再跑下一条，上下文和已完成的结果都往下传。"""
    context, done, rows = {}, {}, []
    for case in cases:
        row = run_one(case, context, done, options, send)
        done[case["name"]] = row
        rows.append(row)
    return rows


def run_parallel(cases, options, send=request_sender.send_case):
    """并发通道：线程池分发，线程之间不共享上下文；同 group 的用例互斥。"""
    if options.workers <= 1 or len(cases) <= 1:
        return [run_one(case, {}, {}, options, send) for case in cases]

    locks = {case["group"]: Lock() for case in cases if case.get("group")}

    def runner(case):
        lock = locks.get(case.get("group") or "")
        if lock is None:
            return run_one(case, {}, {}, options, send)
        with lock:
            return run_one(case, {}, {}, options, send)

    with ThreadPoolExecutor(max_workers=options.workers) as pool:
        return list(pool.map(runner, cases))


def run_all(cases, options=None, send=request_sender.send_case):
    """跑完全部用例：先串行通道，再并发通道，最后按需重跑失败用例。"""
    options = options or RunOptions()
    serial_cases = [case for case in cases if case.get("serial")]
    parallel_cases = [case for case in cases if not case.get("serial")]

    rows = run_serial(serial_cases, options, send)
    if parallel_cases:
        rows.extend(run_parallel(parallel_cases, options, send))

    for round_no in range(1, options.rerun + 1):
        by_name = {row.name: row for row in rows}
        failed_serial = [case for case in serial_cases if by_name[case["name"]].status == FAIL]
        failed_parallel = [case for case in parallel_cases if by_name[case["name"]].status == FAIL]
        if not failed_serial and not failed_parallel:
            break
        options.say(f"第{round_no}轮重跑：串行{len(failed_serial)}条，并发{len(failed_parallel)}条")
        if failed_serial:
            # 串行通道整条重跑，顺序和变量依赖都不能断
            for row in run_serial(serial_cases, options, send):
                old = by_name[row.name]
                if old.status == FAIL and row.status == PASS:
                    old.adopt(row, round_no)
        if failed_parallel:
            for row in run_parallel(failed_parallel, options, send):
                old = by_name[row.name]
                if row.status == PASS:
                    old.adopt(row, round_no)

    rows.sort(key=lambda row: row.index)
    return rows
