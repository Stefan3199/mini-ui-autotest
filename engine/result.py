"""结果模型：判决、原因、单次尝试、单条用例结果。

判决是四态（PASS / FAIL / FLAKY / SKIP），原因（cause）是第二个维度。
两维分开是为了让报告能回答「为什么」，而不是只有一个红叉。
"""

from dataclasses import dataclass, field

PASS = "PASS"
FAIL = "FAIL"
FLAKY = "FLAKY"
SKIP = "SKIP"
STATUSES = (PASS, FAIL, FLAKY, SKIP)


class Cause:
    """判决原因。只有 FAIL / FLAKY / SKIP 才有原因。"""

    ASSERT = "assert"          # 断言没中
    JSON = "json"              # 有响应，但 body 不是 JSON
    CONNECT = "connect"        # 连接被拒、DNS 解析失败
    TIMEOUT = "timeout"        # 超时
    OTHER = "other"            # 其他传输层错误
    VAR = "var"                # ${name} 没定义
    DEPENDENCY = "dependency"  # 依赖的用例没通过
    SETUP = "setup"            # 前置失败
    TEARDOWN = "teardown"      # 后置失败


# 报告里显示的中文标签，key 是 cause
CAUSE_LABELS = {
    Cause.ASSERT: "断言未通过",
    Cause.JSON: "响应不是 JSON",
    Cause.CONNECT: "连接失败",
    Cause.TIMEOUT: "请求超时",
    Cause.OTHER: "传输失败",
    Cause.VAR: "变量未定义",
    Cause.DEPENDENCY: "依赖未通过",
    Cause.SETUP: "前置失败",
    Cause.TEARDOWN: "后置失败",
}


def cause_label(cause):
    return CAUSE_LABELS.get(cause, cause or "")


@dataclass
class Attempt:
    """一次发送尝试。重试几次就有几条，报告里能看到每次错在哪。"""

    attempt: int
    ok: bool
    cause: str = ""
    message: str = ""
    status_code: int | None = None
    elapsed_ms: float = 0.0
    phase: str = "main"  # main / setup:名字 / teardown:名字

    def to_dict(self):
        return {
            "attempt": self.attempt,
            "phase": self.phase,
            "ok": self.ok,
            "cause": self.cause,
            "message": self.message,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class Check:
    """一条断言的结果。"""

    field: str
    op: str
    expect: str
    actual: object
    ok: bool

    def to_dict(self):
        return {
            "field": self.field,
            "op": self.op,
            "expect": self.expect,
            "actual": self.actual,
            "ok": self.ok,
        }


@dataclass
class Outcome:
    """一条用例跑完的结果。报告、JUnit、result.json 都从它派生。"""

    name: str
    index: int = 0
    status: str = PASS
    cause: str = ""
    reason: str = ""
    method: str = ""
    url: str = ""
    params: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    expect_text: str = ""
    actual_text: str = ""
    checks: list = field(default_factory=list)
    saved: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0
    attempts: list = field(default_factory=list)
    lane: str = "并发"
    group: str = ""
    retried: int = 0
    status_code: int | None = None
    body_kind: str = ""  # json / text / empty

    @property
    def failed(self):
        return self.status in (FAIL, FLAKY)

    @property
    def status_label(self):
        return cause_label(self.cause) if self.cause else ""

    def to_dict(self):
        return {
            "index": self.index,
            "name": self.name,
            "status": self.status,
            "cause": self.cause,
            "cause_label": self.status_label,
            "reason": self.reason,
            "method": self.method,
            "url": self.url,
            "status_code": self.status_code,
            "body_kind": self.body_kind,
            "params": self.params,
            "headers": self.headers,
            "expect": self.expect_text,
            "actual": self.actual_text,
            "elapsed_ms": self.elapsed_ms,
            "attempts": [a.to_dict() for a in self.attempts],
            "checks": [c.to_dict() for c in self.checks],
            "saved": self.saved,
            "lane": self.lane,
            "group": self.group,
            "retried": self.retried,
        }

    def adopt(self, other, round_no):
        """重跑通过时，把新结果盖到旧结果上，并记下第几轮通过的。"""
        self.__dict__.update(other.__dict__)
        self.retried = round_no
