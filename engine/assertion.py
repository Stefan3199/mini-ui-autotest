import re

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

def run_checks(result, expect):
    body = result.get("body", {})
    status_code = result.get("status_code")
    rows = []
    for item in expect:
        field = item["field"]
        op = item["op"]
        value = item.get("value")
        if field == "status_code":
            actual = status_code
        else:
            actual = pick(body, field)
        if op == "eq":
            expected = value
            ok = actual == expected
        elif op == "not_null":
            expected = "非空"
            ok = actual not in (None, "")
        elif op == "contains":
            expected = f"包含{value}"
            if isinstance(actual, (str, list)):
                ok = value in actual
            else:
                ok = False
        elif op == "gt":
            expected = f">{value}"
            try:
                ok = actual > value
            except TypeError:
                ok = False
        elif op == "in":
            expected = f"在{value}中"
            if isinstance(value, list):
                ok = actual in value
            else:
                ok = False
        elif op == "regex":
            expected = f"匹配{value}"
            ok = isinstance(actual, str) and re.search(value, actual) is not None
        elif op == "len":
            expected = f"长度={value}"
            try:
                ok = len(actual) == value
            except TypeError:
                ok = False
        elif op == "type":
            expected = f"类型={value}"
            type_map = {"str": str, "int": int, "float": float,
                        "bool": bool, "list": list, "dict": dict, "null": type(None)}
            want = type_map.get(value)
            ok = want is not None and type(actual) is want
        else:
            expected = "?"
            ok = False
        rows.append({"field": field, "expect": expected, "actual": actual, "ok": ok})
    return rows



def describe_expect(expect):
    parts = []
    for item in expect:
        field = item["field"]
        op = item["op"]
        value = item.get("value")
        if op == "eq":
            parts.append(f"{field}={value}")
        elif op == "not_null":
            parts.append(f"{field}非空")
        elif op == "contains":
            parts.append(f"{field}包含{value}")
        elif op == "gt":
            parts.append(f"{field}>{value}")
        elif op == "in":
            parts.append(f"{field}在{value}中")
        elif op == "regex":
            parts.append(f"{field}匹配{value}")
        elif op == "len":
            parts.append(f"{field}长度={value}")
        elif op == "type":
            parts.append(f"{field}类型={value}")
        else:
            parts.append(f"{field}?")
    return "，".join(parts)


def describe_actual(checks):
    parts = []
    for c in checks:
        parts.append(f"{c['field']}={c['actual']}")
    return "，".join(parts)


def describe_bad(bad):
    if bad is None:
        return ""
    return f'{bad["field"]}：期望 {bad["expect"]}，实际 {bad["actual"]}'