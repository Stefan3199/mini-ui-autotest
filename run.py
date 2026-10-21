from engine.case_loader import load_cases, load_config
from engine.request_sender import send_case
from pathlib import Path
import jinja2, sys
import json
import argparse
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from engine.assertion import run_checks, describe_expect, describe_actual, describe_bad, pick
from engine.variables import render, MissingVar

parser = argparse.ArgumentParser()
parser.add_argument("--tag", help="只跑带这个标签的用例")
parser.add_argument("--env", help="环境名，读 config/envs/<name>.yaml")
parser.add_argument("--workers", type=int, default=1, help="并发线程数，默认 1")
parser.add_argument("--only-failed", action="store_true", help="只跑上次失败的用例")
parser.add_argument("--rerun", type=int, default=0, help="失败用例最多再跑几轮，默认 0")
parser.add_argument("--list", action="store_true", help="只列出将要跑的用例，不发请求")
args = parser.parse_args()

all_cases = load_cases(args.env)
cases = all_cases


def with_deps(selected, why):
    """筛选后把依赖一并带进来，否则被选中的用例会因为依赖没跑而全跳。"""
    by_name = {case["name"]: case for case in all_cases}
    chosen = {case["name"] for case in selected}
    added = []
    queue = list(selected)
    while queue:
        case = queue.pop()
        for dep in case.get("depends_on", []):
            if dep not in chosen:
                chosen.add(dep)
                added.append(by_name[dep])
                queue.append(by_name[dep])
    if added:
        print(f"{why}，补上依赖 {len(added)} 条：" + "、".join(case["name"] for case in added))
    return [case for case in all_cases if case["name"] in chosen]


if args.tag:
    cases = [case for case in cases if args.tag in case.get("tags", [])]
    print(f"筛选标签：{args.tag}，共{len(cases)}条")
    cases = with_deps(cases, f"按标签 {args.tag} 筛选")
config = load_config(args.env)
RETRY_COUNT = config["retry_count"]

base = Path(__file__).parent
report_dir = base / config["report_dir"]
report_dir.mkdir(exist_ok=True)
last_failed_file = report_dir / "last-failed.json"

if args.only_failed:
    if not last_failed_file.exists():
        raise SystemExit(f"没有上次的失败清单：{last_failed_file}")
    payload = json.loads(last_failed_file.read_text(encoding="utf-8"))
    names = set(payload.get("failed", []))
    cases = [case for case in cases if case["name"] in names]
    print(f"只跑上次失败的用例，共{len(cases)}条")
    if not cases:
        print("上次没有失败的用例，不用重跑")
        sys.exit(0)
    cases = with_deps(cases, "只跑上次失败的用例")

if args.list:
    for case in cases:
        lane = "串行" if case.get("serial") else "并发"
        tags = ",".join(case.get("tags", [])) or "-"
        print(f'{case["name"]}  [{lane}]  标签 {tags}')
    print(f"共{len(cases)}条")
    sys.exit(0)


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


def run_one(case, context, done=None):
    """跑一条用例：查依赖、替换变量、跑 setup、发主请求、跑 teardown、判三态。"""
    row = {'index': case["index"], 'name': case["name"], 'result': 'PASS', 'expect': '',
           'actual': None, 'method': case["method"], 'url': case["url"], 'params': {},
           'reason': '', 'checks': [], 'headers': {}, 'elapsed': 0.0, 'saved': {},
           'lane': '串行' if case.get("serial") else '并发', 'retried': 0}
    start = time.perf_counter()

    for dep in case.get("depends_on", []):
        dep_row = (done or {}).get(dep)
        if dep_row is None or dep_row["result"] != "PASS":
            row['result'] = 'SKIP'
            row['reason'] = f'依赖 {dep} 未通过'
            row['elapsed'] = round((time.perf_counter() - start) * 1000, 1)
            print(row['name'], 'SKIP', row['reason'])
            return row

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


def run_alone(case):
    """并发通道里的单条用例，各拿各的空上下文。"""
    return run_one(case, {}, {})


def run_serial(batch):
    """串行通道：一条跑完再跑下一条，上下文和已完成的结果都往下传。"""
    context = {}
    done = {}
    rows = []
    for case in batch:
        row = run_one(case, context, done)
        done[case["name"]] = row
        rows.append(row)
    return rows


def run_parallel(batch, workers):
    """并发通道：线程池分发，线程之间不共享上下文。"""
    if workers > 1 and len(batch) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(run_alone, batch))
    return [run_one(case, {}, {}) for case in batch]


def write_junit(rows, path, suite_name, wall_s):
    """写 JUnit XML，CI 平台认这个格式。FAIL 用 failure，FLAKY 用 error，SKIP 用 skipped。"""
    failures = sum(1 for row in rows if row["result"] == "FAIL")
    errors = sum(1 for row in rows if row["result"] == "FLAKY")
    skipped = sum(1 for row in rows if row["result"] == "SKIP")
    root = ET.Element("testsuites", name="mini-ui-autotest", tests=str(len(rows)),
                      failures=str(failures), errors=str(errors), skipped=str(skipped),
                      time=f"{wall_s:.3f}")
    suite = ET.SubElement(root, "testsuite", name=suite_name, tests=str(len(rows)),
                          failures=str(failures), errors=str(errors), skipped=str(skipped),
                          time=f"{wall_s:.3f}",
                          timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"))
    for row in rows:
        item = ET.SubElement(suite, "testcase", classname="mini-ui-autotest",
                             name=row["name"], time=f'{row["elapsed"] / 1000:.3f}')
        if row["result"] == "FAIL":
            failure = ET.SubElement(item, "failure", message=row["reason"] or "断言未通过")
            failure.text = f'预期：{row["expect"]}\n实际：{row["actual"]}'
        elif row["result"] == "FLAKY":
            ET.SubElement(item, "error", message=row["reason"] or "未拿到响应")
        elif row["result"] == "SKIP":
            ET.SubElement(item, "skipped", message=row["reason"] or "依赖未通过")
        if row["retried"]:
            note = ET.SubElement(item, "system-out")
            note.text = f'第{row["retried"]}轮重跑后通过'
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


serial_cases = [case for case in cases if case.get("serial")]
parallel_cases = [case for case in cases if not case.get("serial")]

results = []
run_start = time.perf_counter()
results.extend(run_serial(serial_cases))

if parallel_cases:
    results.extend(run_parallel(parallel_cases, args.workers))

retried_total = 0
for round_no in range(1, args.rerun + 1):
    row_by_name = {row["name"]: row for row in results}
    failed_serial = [case for case in serial_cases if row_by_name[case["name"]]["result"] == "FAIL"]
    failed_parallel = [case for case in parallel_cases if row_by_name[case["name"]]["result"] == "FAIL"]
    if not failed_serial and not failed_parallel:
        break
    print(f"第{round_no}轮重跑：串行{len(failed_serial)}条，并发{len(failed_parallel)}条")
    if failed_serial:
        # 串行通道整条重跑，顺序和变量依赖都不能断
        for row in run_serial(serial_cases):
            old = row_by_name[row["name"]]
            if old["result"] == "FAIL" and row["result"] == "PASS":
                row["retried"] = round_no
                old.update(row)
                retried_total += 1
    if failed_parallel:
        for row in run_parallel(failed_parallel, args.workers):
            old = row_by_name[row["name"]]
            if row["result"] == "PASS":
                row["retried"] = round_no
                old.update(row)
                retried_total += 1

results.sort(key=lambda row: row["index"])

wall_ms = round((time.perf_counter() - run_start) * 1000, 1)
case_sum = round(sum(row["elapsed"] for row in results), 1)
passed = sum(1 for row in results if row["result"] == "PASS")
failed = sum(1 for row in results if row["result"] == "FAIL")
flaky = sum(1 for row in results if row["result"] == "FLAKY")
skipped = sum(1 for row in results if row["result"] == "SKIP")
slowest = max(results, key=lambda row: row["elapsed"]) if results else None

tail = f"，重跑通过{retried_total}条" if retried_total else ""
print(f"共{len(cases)}条，过{passed}，挂{failed}，抖{flaky}，跳{skipped}{tail}")

now = time.strftime("%Y-%m-%d %H:%M")
failed_names = [row["name"] for row in results if row["result"] in ("FAIL", "FLAKY")]
last_failed_file.write_text(
    json.dumps({"time": now, "env": args.env or "default", "failed": failed_names},
               ensure_ascii=False, indent=2),
    encoding="utf-8",
)

history_file = report_dir / "history.json"
history = []
if history_file.exists():
    history = json.loads(history_file.read_text(encoding="utf-8"))
history.append({
    "time": now,
    "env": args.env or "default",
    "tag": args.tag or "全部",
    "total": len(cases),
    "passed": passed,
    "failed": failed,
    "flaky": flaky,
    "retried": retried_total,
    "wall_ms": wall_ms,
})
history = history[-20:]
history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
recent = list(reversed(history[-5:]))

trend = []
for item in history[-10:]:
    total_run = item["total"] or 1
    trend.append({
        "label": item["time"][11:],
        "passed": item["passed"],
        "total": item["total"],
        "rate": round(item["passed"] / total_run * 100),
        "height": 6 + round(item["passed"] / total_run * 55),
    })

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
    skipped=skipped,
    flaky=flaky,
    retried=retried_total,
    tag=args.tag or "全部",
    total_ms=wall_ms,
    case_sum=case_sum,
    slowest=slowest,
    env_name=args.env or "default",
    workers=args.workers,
    recent=recent,
    trend=trend,
)

report_file = report_dir / "report.html"
report_file.write_text(html, encoding="utf-8")

junit_file = report_dir / "junit.xml"
write_junit(results, junit_file, args.env or "default", wall_ms / 1000)

archive_dir = report_dir / "archive"
archive_dir.mkdir(exist_ok=True)
stamp = time.strftime("%Y%m%d-%H%M%S")
tag_part = args.tag or "all"
archive_file = archive_dir / f'{stamp}_{args.env or "default"}_{tag_part}.html'
archive_file.write_text(html, encoding="utf-8")
for old in sorted(archive_dir.glob("*.html"))[:-20]:
    old.unlink()

print("报告生成好了:", report_file)
print("JUnit 报告:", junit_file)
print("本次归档:", archive_file)


if failed > 0 or flaky > 0:
    sys.exit(1)
