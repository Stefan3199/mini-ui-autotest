"""出报告：HTML、JUnit、result.json、运行历史、归档、失败清单。

报告、JUnit、result.json 三份产物都从同一批 Outcome 派生，
不会有「HTML 说挂了、JUnit 说过了」这种事。
"""

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import jinja2

from . import __version__
from .result import FAIL, FLAKY, PASS, SKIP, cause_label

MAX_HISTORY = 20
RECENT_RUNS = 5
TREND_RUNS = 10


def summarize(rows, wall_ms=0.0):
    """总账。墙钟耗时和用例耗时合计分开算，并发时两个数差多少一目了然。"""
    total = len(rows)
    passed = sum(1 for row in rows if row.status == PASS)
    failed = sum(1 for row in rows if row.status == FAIL)
    flaky = sum(1 for row in rows if row.status == FLAKY)
    skipped = sum(1 for row in rows if row.status == SKIP)
    slowest = max(rows, key=lambda row: row.elapsed_ms) if rows else None
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "flaky": flaky,
        "skipped": skipped,
        "retried": sum(1 for row in rows if row.retried),
        "wall_ms": round(wall_ms, 1),
        "case_sum": round(sum(row.elapsed_ms for row in rows), 1),
        "pass_rate": round(passed / total * 100) if total else 0,
        "slowest": slowest,
    }


def load_history(path):
    path = Path(path)
    if not path.exists():
        return []
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return history if isinstance(history, list) else []


def build_trend(history):
    """最近十次通过率，柱高按通过率算。"""
    trend = []
    for entry in history[-TREND_RUNS:]:
        total = entry.get("total") or 1
        passed = entry.get("passed", 0)
        trend.append({
            "label": (entry.get("time") or "")[11:],
            "passed": passed,
            "total": entry.get("total", 0),
            "rate": round(passed / total * 100),
            "height": 6 + round(passed / total * 55),
        })
    return trend


def render_html(rows, summary, meta, history, template_dir):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        autoescape=True,
    )
    template = env.get_template("report.html")
    return template.render(
        rows=rows,
        summary=summary,
        meta=meta,
        recent=list(reversed(history[-RECENT_RUNS:])),
        trend=build_trend(history),
    )


def write_junit(rows, path, suite_name, wall_s):
    """写 JUnit XML。FAIL 记 failure，FLAKY 记 error，SKIP 记 skipped。"""
    failures = sum(1 for row in rows if row.status == FAIL)
    errors = sum(1 for row in rows if row.status == FLAKY)
    skipped = sum(1 for row in rows if row.status == SKIP)
    root = ET.Element("testsuites", name="mini-ui-autotest", tests=str(len(rows)),
                      failures=str(failures), errors=str(errors), skipped=str(skipped),
                      time=f"{wall_s:.3f}")
    suite = ET.SubElement(root, "testsuite", name=suite_name, tests=str(len(rows)),
                          failures=str(failures), errors=str(errors), skipped=str(skipped),
                          time=f"{wall_s:.3f}",
                          timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"))
    for row in rows:
        case = ET.SubElement(suite, "testcase", classname="mini-ui-autotest",
                             name=row.name, time=f"{row.elapsed_ms / 1000:.3f}")
        label = cause_label(row.cause)
        message = "；".join(part for part in (label, row.reason) if part)
        if row.status == FAIL:
            failure = ET.SubElement(case, "failure", message=message or "断言未通过")
            failure.text = f"预期：{row.expect_text}\n实际：{row.actual_text}"
        elif row.status == FLAKY:
            ET.SubElement(case, "error", message=message or "未拿到响应")
        elif row.status == SKIP:
            ET.SubElement(case, "skipped", message=message or "依赖未通过")
        if row.retried:
            note = ET.SubElement(case, "system-out")
            note.text = f"第{row.retried}轮重跑后通过"
    path = Path(path)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_result_json(rows, summary, meta, path):
    """机器可读的结果：CI、看板、脚本用这个，不用解析 HTML。"""
    payload = {
        "meta": {"tool": "mini-ui-autotest", "version": __version__, **meta},
        "summary": {key: value for key, value in summary.items() if key != "slowest"},
        "cases": [row.to_dict() for row in rows],
    }
    if summary["slowest"] is not None:
        payload["summary"]["slowest"] = summary["slowest"].name
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_last_failed(path, rows, meta):
    """记下这轮挂的和抖的，--only-failed 下一轮只跑这些。"""
    failed = [row.name for row in rows if row.status in (FAIL, FLAKY)]
    skipped = [row.name for row in rows if row.status == SKIP]
    payload = {"time": meta.get("time", ""), "env": meta.get("env") or "default",
               "failed": failed, "skipped": skipped}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return failed


def archive(report_dir, html, meta, keep):
    """存一份带时间戳的快照，只留最近 keep 份。"""
    archive_dir = Path(report_dir) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = meta.get("stamp") or time.strftime("%Y%m%d-%H%M%S")
    tag = str(meta.get("tag") or "全部").replace("/", "-").replace("\\", "-").replace(" ", "_")
    env = meta.get("env") or "default"
    path = archive_dir / f"{stamp}_{env}_{tag}.html"
    suffix = 2
    while path.exists():
        path = archive_dir / f"{stamp}_{env}_{tag}-{suffix}.html"
        suffix += 1
    path.write_text(html, encoding="utf-8")
    for old in sorted(archive_dir.glob("*.html"))[:-keep]:
        old.unlink()
    return path


def _history_entry(rows, summary, meta):
    return {
        "time": meta.get("time", ""),
        "env": meta.get("env") or "default",
        "tag": meta.get("tag") or "全部",
        "total": summary["total"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "flaky": summary["flaky"],
        "retried": summary["retried"],
        "wall_ms": summary["wall_ms"],
    }


def write_reports(rows, meta, report_dir, template_dir, keep=20, wall_ms=0.0):
    """一次性写全部产物，返回路径和总账。"""
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(rows, wall_ms)
    meta = {**meta, "version": __version__}

    history_path = report_dir / "history.json"
    history = load_history(history_path)
    history.append(_history_entry(rows, summary, meta))
    history = history[-MAX_HISTORY:]
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    html = render_html(rows, summary, meta, history, template_dir)
    report_path = report_dir / "report.html"
    report_path.write_text(html, encoding="utf-8")

    junit_path = report_dir / "junit.xml"
    write_junit(rows, junit_path, meta.get("env") or "default", summary["wall_ms"] / 1000)

    json_path = report_dir / "result.json"
    write_result_json(rows, summary, meta, json_path)

    last_failed_path = report_dir / "last-failed.json"
    write_last_failed(last_failed_path, rows, meta)

    archive_path = archive(report_dir, html, meta, keep)

    return {
        "summary": summary,
        "html": report_path,
        "junit": junit_path,
        "json": json_path,
        "archive": archive_path,
        "history": history_path,
        "last_failed": last_failed_path,
    }
