"""命令行入口：解析参数、加载用例、调度、出报告、定退出码。

退出码是给 CI 看的契约：
    0 全过 / 1 有 FAIL / 2 只有 FLAKY / 3 用例、配置或参数写错了
"""

import argparse
import json
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__, case_loader, report, runner

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_FLAKY = 2
EXIT_BAD_INPUT = 3


class Parser(argparse.ArgumentParser):
    """参数写错也走 3，和「有失败」的 1、2 分开。"""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: 参数错误：{message}", file=sys.stderr)
        raise SystemExit(EXIT_BAD_INPUT)


def build_parser():
    parser = Parser(prog="run.py", description="mini-ui-autotest：YAML 数据驱动的接口自动化测试")
    parser.add_argument("--tag", help="只跑带这个标签的用例")
    parser.add_argument("--env", help="环境名，读 config/envs/<name>.yaml")
    parser.add_argument("--base-url", dest="base_url", help="覆盖配置里的 base_url")
    parser.add_argument("--report-dir", dest="report_dir", help="覆盖配置里的 report_dir")
    parser.add_argument("--retry", type=int, help="覆盖 retry_count（单条请求最多尝试次数）")
    parser.add_argument("--timeout", type=float, help="覆盖默认超时秒数，用例自己写的 timeout 仍然优先")
    parser.add_argument("--workers", type=int, default=1, help="并发线程数，默认 1")
    parser.add_argument("--only-failed", action="store_true", help="只跑上次失败的用例")
    parser.add_argument("--rerun", type=int, default=0, help="失败用例最多再跑几轮，默认 0")
    parser.add_argument("--list", action="store_true", help="只列出将要跑的用例，不发请求")
    parser.add_argument("--strict", action="store_true", help="用例里出现未知字段直接报错")
    parser.add_argument("--keep", type=int, help="归档保留份数，覆盖 config 里的 archive_keep")
    parser.add_argument("--open", action="store_true", help="跑完直接打开报告")
    parser.add_argument("--quiet", action="store_true", help="不打印每条用例的结果")
    parser.add_argument("--verbose", action="store_true", help="打印每次请求尝试")
    parser.add_argument("--version", action="version", version=f"mini-ui-autotest {__version__}")
    return parser


def _force_utf8():
    """被重定向或进 CI 时按 UTF-8 输出，控制台保持系统默认。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        if getattr(stream, "isatty", lambda: False)():
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def with_deps(selected, all_cases, why):
    """筛选后把依赖一并带进来，否则被选中的用例会因为依赖没跑而全跳。"""
    by_name = {case["name"]: case for case in all_cases}
    chosen = {case["name"] for case in selected}
    added = []
    queue = list(selected)
    while queue:
        case = queue.pop()
        for dep in case.get("depends_on") or []:
            if dep not in chosen:
                chosen.add(dep)
                added.append(by_name[dep])
                queue.append(by_name[dep])
    if added:
        print(f"{why}，补上依赖 {len(added)} 条：" + "、".join(case["name"] for case in added))
    return [case for case in all_cases if case["name"] in chosen]


def print_list(cases):
    for case in cases:
        lane = "串行" if case.get("serial") else "并发"
        tags = ",".join(case.get("tags", [])) or "-"
        detail = ""
        if case.get("serial"):
            detail = f"  （{('、'.join(case.get('serial_reasons') or [])) or '串行'}）"
        group = f"  组 {case['group']}" if case.get("group") else ""
        print(f'{case["name"]}  [{lane}]  标签 {tags}{group}{detail}')
    print(f"共{len(cases)}条")


def _load_last_failed(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    names = list(payload.get("failed") or [])
    names += [name for name in (payload.get("skipped") or []) if name not in names]
    return names


def main(argv=None):
    _force_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers 至少是 1")
    if args.rerun < 0:
        parser.error("--rerun 不能是负数")
    if args.keep is not None and args.keep < 1:
        parser.error("--keep 至少是 1")
    if args.retry is not None and args.retry < 1:
        parser.error("--retry 至少是 1")
    if args.timeout is not None and args.timeout <= 0:
        parser.error("--timeout 要大于 0")
    if args.base_url and not args.base_url.startswith(("http://", "https://")):
        parser.error("--base-url 要以 http:// 或 https:// 开头")

    overrides = {}
    if args.base_url:
        overrides["base_url"] = args.base_url.rstrip("/")
    if args.report_dir:
        overrides["report_dir"] = args.report_dir
    if args.retry is not None:
        overrides["retry_count"] = args.retry
    if args.timeout is not None:
        overrides["timeout"] = args.timeout

    base = Path(__file__).resolve().parents[1]
    started = time.perf_counter()

    try:
        config = case_loader.load_config(args.env, base, overrides)
        loaded = case_loader.load_cases(args.env, strict=args.strict, base_dir=base, config=config)
    except case_loader.CaseError as error:
        print(f"用例或配置有问题：{error}", file=sys.stderr)
        return EXIT_BAD_INPUT

    for warning in loaded.warnings:
        print(f"提示：{warning}", file=sys.stderr)

    all_cases = loaded.cases
    cases = all_cases
    report_dir = base / config["report_dir"]

    if args.tag:
        cases = [case for case in cases if args.tag in case.get("tags", [])]
        print(f"筛选标签：{args.tag}，共{len(cases)}条")
        cases = with_deps(cases, all_cases, f"按标签 {args.tag} 筛选")

    if args.only_failed:
        last_failed_file = report_dir / "last-failed.json"
        if not last_failed_file.exists():
            print(f"没有上次的失败清单：{last_failed_file}", file=sys.stderr)
            return EXIT_BAD_INPUT
        names = set(_load_last_failed(last_failed_file))
        cases = [case for case in cases if case["name"] in names]
        print(f"只跑上次失败的用例，共{len(cases)}条")
        if not cases:
            print("上次没有失败的用例，不用重跑")
            return EXIT_OK
        cases = with_deps(cases, all_cases, "只跑上次失败的用例")

    if args.list:
        print_list(cases)
        return EXIT_OK

    options = runner.RunOptions(
        workers=args.workers,
        rerun=args.rerun,
        retry_count=config["retry_count"],
        quiet=args.quiet,
        verbose=args.verbose,
        sensitive=tuple(config.get("sensitive") or ()),
        retry_backoff=config.get("retry_backoff", 0),
    )
    rows = runner.run_all(cases, options)

    wall_ms = round((time.perf_counter() - started) * 1000, 1)
    meta = {
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "stamp": time.strftime("%Y%m%d-%H%M%S"),
        "env": args.env or "default",
        "tag": args.tag or "全部",
        "workers": args.workers,
        "base_url": config["base_url"],
        "strict": args.strict,
        "rerun": args.rerun,
        "duration_ms": wall_ms,
    }
    paths = report.write_reports(
        rows, meta, report_dir, base / "templates",
        keep=args.keep or config["archive_keep"], wall_ms=wall_ms,
    )
    summary = paths["summary"]

    tail = f"，重跑通过{summary['retried']}条" if summary["retried"] else ""
    print(f"共{summary['total']}条，过{summary['passed']}，挂{summary['failed']}，"
          f"抖{summary['flaky']}，跳{summary['skipped']}{tail}")
    print("报告生成好了:", paths["html"])
    print("JUnit 报告:", paths["junit"])
    print("机器可读结果:", paths["json"])
    print("本次归档:", paths["archive"])

    if args.open:
        try:
            webbrowser.open(paths["html"].as_uri())
        except Exception:  # 打不开浏览器不该影响结果
            print("打不开浏览器，报告路径在上面", file=sys.stderr)

    if summary["failed"]:
        return EXIT_FAILED
    if summary["flaky"]:
        return EXIT_FLAKY
    return EXIT_OK
