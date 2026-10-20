def run_checks(result, expect):
    rows = []
    for item in expect:
        field = item["field"]
        op = item["op"]
        actual = result.get(field)
        if op == "eq":
            expected = item["value"]
            ok = actual == expected
        elif op == "not_null":
            expected = "非空"
            ok = actual not in (None, "")
        else:
            expected = "?"
            ok = False
        rows.append({"field": field, "expect": expected, "actual": actual, "ok": ok})
    return rows


def describe_expect(expect):
    parts = []
    for item in expect:
        if item["op"] == "eq":
            parts.append(f'{item["field"]}={item["value"]}')
        elif item["op"] == "not_null":
            parts.append(f'{item["field"]}非空')
        else:
            parts.append(f'{item["field"]}?')
    return "，".join(parts)


def describe_actual(result, expect):
    parts = []
    for item in expect:
        field = item["field"]
        parts.append(f'{field}={result.get(field)}')
    return "，".join(parts)


def describe_bad(bad):
    if bad is None:
        return ""
    return f'{bad["field"]}：期望 {bad["expect"]}，实际 {bad["actual"]}'