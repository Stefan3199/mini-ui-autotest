from engine.case_loader import load_cases, load_config
from engine.request_sender import send_case
from pathlib import Path
import jinja2, sys
import argparse
from engine.assertion import run_checks, describe_expect, describe_actual, describe_bad, pick
from engine.variables import render, MissingVar
import time

parser = argparse.ArgumentParser()
parser.add_argument("--tag", help="只跑带这个标签的用例")
args = parser.parse_args()
cases = load_cases()
if args.tag:
    cases = [case for case in cases if args.tag in case.get("tags", [])]
    print(f"筛选标签：{args.tag}，共{len(cases)}条")
config = load_config()
RETRY_COUNT = config["retry_count"]

passed = 0
failed = 0
flaky = 0
results = []
context = {}

for case in cases:
    missing = None
    try:
        case = render(case, {**context, **case.get("vars", {})})
    except MissingVar as e:
        missing = str(e)

    if missing is not None:
        failed = failed + 1
        print(case["name"], "FAIL", f"变量 ${{{missing}}} 没定义")
        results.append({'name': case["name"], 'result': 'FAIL', 'expect': '', 'actual': None,
                        'method': case["method"], 'url': case["url"], 'params': {}, 'reason': f'变量 ${{{missing}}} 没定义',
                        'checks': [], 'headers': {}, 'elapsed': 0, 'saved': {}})
        continue

    case_start = time.perf_counter()
    result = None

    for attempt in range(RETRY_COUNT):
        result = send_case(case)
        if result is not None:
            break   # 拿到判决了，这趟不用再试，跳出发几次的圈

    case_ms = round((time.perf_counter() - case_start) * 1000, 1)

    safe_params = case["params"].copy()
    safe_headers = case["headers"].copy()
    for key in safe_headers:
        if "token" in key.lower() or "auth" in key.lower() or "key" in key.lower():
            safe_headers[key] = "***"

    for key in safe_params:
        if "pwd" in key or "pass" in key:
            safe_params[key] = "***"
    checks = []

    bad = None
    if result is not None:
        checks = run_checks(result, case["expect"])
        for row in checks:
            if not row["ok"]:
                bad = row
                break

    saved_now = {}
    if result is not None and bad is None and case.get("save"):
        for var, field in case["save"].items():
            value = pick(result["body"], field)
            context[var] = value
            saved_now[var] = value

    expect_text = describe_expect(case["expect"])
    actual_text = describe_actual(checks) if result is not None else None
    reason_text = describe_bad(bad)

    if result is None:
        flaky = flaky + 1
        print(case["name"], "环境抖动")
        results.append({'name': case["name"], 'result': 'FLAKY', 'expect': expect_text, 'actual': None, 'method': case["method"], 'url': case["url"], 'params': safe_params, 'reason': reason_text, 'checks': checks, 'headers': safe_headers, 'elapsed': case_ms, 'saved': saved_now})
    elif bad is None:
        passed = passed + 1
        print(case["name"], 'PASS')
        results.append({'name': case["name"], 'result': 'PASS', 'expect': expect_text, 'actual': actual_text, 'method': case["method"], 'url': case["url"], 'params': safe_params, 'reason': reason_text, 'checks': checks, 'headers': safe_headers, 'elapsed': case_ms, 'saved': saved_now})
    else:
        failed = failed + 1
        print(case["name"], 'FAIL', reason_text)
        results.append({'name': case["name"], 'result': 'FAIL', 'expect': expect_text, 'actual': actual_text, 'method': case["method"], 'url': case["url"], 'params': safe_params, 'reason': reason_text, 'checks': checks, 'headers': safe_headers, 'elapsed': case_ms, 'saved': saved_now})

total_ms = round(sum(row["elapsed"] for row in results), 1)
slowest = max(results, key=lambda row: row["elapsed"]) if results else None

print(f"共{len(cases)}条，过{passed}，挂{failed}，抖{flaky}")

base = Path(__file__).parent
report_dir = base / config["report_dir"]
report_dir.mkdir(exist_ok=True)

env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(base / "templates")),
    autoescape=True,
)
tpl = env.get_template("report.html")
html = tpl.render(
    results=results,
    total=len(cases),
    passed=passed,
    failed=failed,
    flaky=flaky,
    tag=args.tag or "全部",
    total_ms=total_ms,
    slowest=slowest,
)
(report_dir / "report.html").write_text(html, encoding="utf-8")
print("报告生成好了:", report_dir / "report.html")


if failed > 0 or flaky > 0:
    sys.exit(1)