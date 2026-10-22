"""测试公用的夹具和小工具。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.request_sender import Response  # noqa: E402
from engine.result import Outcome  # noqa: E402

BASE_CONFIG = {
    "base_url": "http://127.0.0.1:9300",
    "report_dir": "report",
    "retry_count": 3,
    "timeout": 5,
    "archive_keep": 20,
    "headers": {"X-Token": "default-token"},
    "sensitive": ["pwd", "pass", "token", "auth", "key", "secret", "cookie", "session"],
}


def make_case(**overrides):
    """造一条「已经拼好 URL」的用例，直接喂给 runner。"""
    case = {
        "name": "用例",
        "index": 1,
        "url": "http://127.0.0.1:9300/echo",
        "method": "GET",
        "params": {},
        "headers": {},
        "timeout": 5,
        "expect": [{"field": "code", "op": "eq", "value": 0}],
        "setup": [],
        "teardown": [],
        "depends_on": [],
        "serial": False,
    }
    case.update(overrides)
    return case


def ok_response(body=None, status=200, text="", kind="json"):
    return Response(status_code=status, body={"code": 0} if body is None else body,
                    text=text, elapsed_ms=1.0, body_kind=kind)


def scripted_send(results):
    """按顺序返回或抛出，用完之后重复最后一个；send.calls 记调用次数。"""
    queue = list(results)
    record = {"count": 0, "requests": []}

    def send(case):
        record["count"] += 1
        record["requests"].append(case)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(case)
        return item

    send.record = record
    return send


def outcome(name, status="PASS", cause=""):
    return Outcome(name=name, status=status, cause=cause)
