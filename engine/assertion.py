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
            actual = body.get(field)
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