from engine.case_loader import load_cases, load_config
from engine.request_sender import send_case
from pathlib import Path
import jinja2, sys
import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from engine.assertion import run_checks, describe_expect, describe_actual, describe_bad, pick
from engine.variables import render, MissingVar

parser = argparse.ArgumentParser()
parser.add_argument("--tag", help="只跑带这个标签的用例")
parser.add_argument("--env", help="环境名，读 config/envs/<name>.yaml")
parser.add_argument("--workers", type=int, default=1, help="并发线程数，默认 1")
args = parser.parse_args()

cases = load_cases(args.env)
if args.tag:
    cases = [case for case in cases if args.tag in case.get("tags", [])]
    print(f"筛选标签：{args.tag}，共{len(cases)}条")
config = load_config(args.env)
RETRY_COUNT = config["retry_count"]


def call(request):
    """发一条请求，拿不到响应就按 retry_count 重试。"""
    for attempt in range(RETRY_COUNT):
        result = send_case(request)
        if result is not None:
            return result
    return None


def mask_params(params):
    safe = dict(params)
    for key in safe:
        if "pwd" in key or "pass" in key:
            safe[key] = "***"
    return safe


def mask_headers(headers):
    safe = dict(headers)
    for key in safe:
        if "token" in key.lower() or "auth" in key.lower() or "key" in key.lower():
            safe[key] = "***"
    return safe


def run_one(case, context):
    """跑一条用例：替换变量、跑 setup、发主请求、跑 teardown、判三态。"""
    row = {'index': case["index"], 'name': case["name"], 'result': 'PASS', 'expect': '',
           'actual': None, 'method': case["method"], 'url': case["url"], 'params': {},
           'reason': '', 'checks': [], 'headers': {}, 'elapsed': 0.0, 'saved': {}}
    start = time.perf_counter()

    try:
        case = render(case, {**context, **case.get("vars", {})})
    except MissingVar as e:
        row['result'] = 'FAIL'
        row['reason'] = f'变量 ${{{e}}} 没定义'
        row['elapsed'] = round((time.perf_counter() - start) * 1000, 1)
        print(row['name'], 'FAIL', row['reason'])
        return row

    for step in case.get("setup", []):
        if call(step) is None:
            row['result'] = 'FAIL'
            row['reason'] = f'setup 失败：{step["name"]}'
            row['elapsed'] = round((time.perf_counter() - start) * 1000, 1)
            print(row['name'], 'FAIL', row['reason'])
            return row

    result = call(case)

    teardown_failed = None
    for step in case.get("teardown", []):
        if call(step) is None and teardown_failed is None:
            teardown_failed = step["name"]

    row['elapsed'] = round((time.perf_counter() - start) * 1000, 1)
    row['params'] = mask_params(case["params"])
    row['headers'] = mask_headers(case["headers"])
    row['expect'] = describe_expect(case["expect"])

    bad = None
    if result is not None:
        row['checks'] = run_checks(result, case["expect"])
        for check in row['checks']:
            if not check["ok"]:
                bad = check
                break
        row['actual'] = describe_actual(row['checks'])

    if result is None:
        row['result'] = 'FLAKY'
        row['reason'] = '环境抖动'
    elif bad is not None:
        row['result'] = 'FAIL'
        row['reason'] = describe_bad(bad)
    elif teardown_failed is not None:
        row['result'] = 'FAIL'
        row['reason'] = f'teardown 失败：{teardown_failed}'
    else:
        row['result'] = 'PASS'

    if row['result'] == 'PASS' and case.get("save"):
        for var, field in case["save"].items():
            value = pick(result["body"], field)
            context[var] = value
            row['saved'][var] = value

    print(row['name'], row['result'], row['reason'])
    return row


ordered = [case for case in cases if case.get("serial")]
parallel = [case for case in cases if not case.get("serial")]

context = {}
results = []
run_start = time.perf_counter()
for case in ordered:
    results.append(run_one(case, context))

if args.workers > 1 and parallel:
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results.extend(pool.map(lambda case: run_one(case, {}), parallel))
else:
    for case in parallel:
        results.append(run_one(case, {}))

results.sort(key=lambda row: row["index"])

wall_ms = round((time.perf_counter() - run_start) * 1000, 1)
case_sum = round(sum(row["elapsed"] for row in results), 1)
passed = sum(1 for row in results if row["result"] == "PASS")
failed = sum(1 for row in results if row["result"] == "FAIL")
flaky = sum(1 for row in results if row["result"] == "FLAKY")
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
    total_ms=wall_ms,
    case_sum=case_sum,
    slowest=slowest,
    env_name=args.env or "default",
    workers=args.workers,
)
(report_dir / "report.html").write_text(html, encoding="utf-8")
print("报告生成好了:", report_dir / "report.html")


if failed > 0 or flaky > 0:
    sys.exit(1)