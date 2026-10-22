"""断言引擎：算子表、嵌套字段取值、逐条结果。

算子写成一张表，run_checks 和 describe_expect 共用，加算子只改一处。
"""

import re


class Op:
    """一个断言算子：怎么判、怎么在报告里描述、要不要 value。

    describe 是断言明细里「期望」列的文字，summary 是总览行里
    「字段+期望」的文字，默认两者一样（eq 的 summary 前面多个等号）。
    """

    __slots__ = ("fn", "describe", "summary", "needs_value")

    def __init__(self, fn, describe, summary=None, needs_value=True):
        self.fn = fn
        self.describe = describe
        self.summary = summary or describe
        self.needs_value = needs_value


def _eq(actual, value):
    return actual == value


def _not_null(actual, value):
    return actual not in (None, "")


def _contains(actual, value):
    if isinstance(actual, (str, list)):
        return value in actual
    return False


def _gt(actual, value):
    try:
        return actual > value
    except TypeError:
        return False


def _in(actual, value):
    if isinstance(value, list):
        return actual in value
    return False


def _regex(actual, value):
    if not isinstance(actual, str):
        return False
    try:
        return re.search(value, actual) is not None
    except re.error:
        return False


def _len(actual, value):
    try:
        return len(actual) == value
    except TypeError:
        return False


TYPE_MAP = {"str": str, "int": int, "float": float,
            "bool": bool, "list": list, "dict": dict, "null": type(None)}


def _type(actual, value):
    want = TYPE_MAP.get(value)
    return want is not None and type(actual) is want


OPS = {
    "eq": Op(_eq, lambda value: f"{value}", summary=lambda value: f"={value}"),
    "not_null": Op(_not_null, lambda value: "非空", needs_value=False),
    "contains": Op(_contains, lambda value: f"包含{value}"),
    "gt": Op(_gt, lambda value: f">{value}"),
    "in": Op(_in, lambda value: f"在{value}中"),
    "regex": Op(_regex, lambda value: f"匹配{value}"),
    "len": Op(_len, lambda value: f"长度={value}"),
    "type": Op(_type, lambda value: f"类型={value}"),
}

OP_NAMES = tuple(OPS)

# 特殊字段：判 HTTP 状态码，其余都从响应 JSON 里取
STATUS_FIELD = "status_code"


def pick(body, field):
    """按 data.user.id 这种路径取值，中间任何一层取不到就返回 None。"""
    current = body
    for part in field.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def run_checks(status_code, body, expect):
    """跑一遍断言，返回 Check 列表。算子在加载期已校验，这里不再兜底。"""
    from .result import Check

    rows = []
    for item in expect:
        field = item["field"]
        op_name = item["op"]
        value = item.get("value")
        op = OPS.get(op_name)
        if op is None:
            raise ValueError(f"不认识的 op：{op_name}")
        actual = status_code if field == STATUS_FIELD else pick(body, field)
        rows.append(Check(
            field=field,
            op=op_name,
            expect=op.describe(value),
            actual=actual,
            ok=bool(op.fn(actual, value)),
        ))
    return rows


def validate_item(item):
    """加载期校验一条断言，返回错误描述或 None。"""
    if not isinstance(item, dict):
        return "断言要写成 {field, op, value} 这样的字典"
    field = item.get("field")
    if not isinstance(field, str) or not field.strip():
        return "断言缺少 field"
    op_name = item.get("op")
    if op_name not in OPS:
        return f'断言 {field}：不认识的 op「{op_name}」，可用：{", ".join(OP_NAMES)}'
    if OPS[op_name].needs_value and "value" not in item:
        return f'断言 {field}：op={op_name} 需要 value'
    return None


def describe_expect(expect):
    parts = []
    for item in expect:
        field = item["field"]
        op = OPS.get(item["op"])
        parts.append(f"{field}{op.summary(item.get('value')) if op else '?'}")
    return "，".join(parts)


def describe_actual(checks):
    return "，".join(f"{check.field}={check.actual}" for check in checks)


def describe_bad(bad):
    if bad is None:
        return ""
    return f'{bad.field}：期望 {bad.expect}，实际 {bad.actual}'
