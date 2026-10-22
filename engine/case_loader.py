"""读配置和用例：校验、深合并环境、展开参数化、排依赖、拼请求。

这一层是唯一的「用例输入口」。它保证交出去的用例是校验过的、拼好 URL 的、
并且不共享任何可变对象（展开和拼接都返回新字典），上层不用再操心这些。
"""

import copy
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import assertion, masking, variables
from .request_sender import METHODS

DEFAULT_ARCHIVE_KEEP = 20

# 用例允许出现的字段，多出来的字段默认警告，--strict 下报错
ALLOWED_KEYS = {
    "name", "tags", "url", "method", "headers", "params", "timeout",
    "expect", "save", "data", "data_file", "serial", "group",
    "depends_on", "setup", "teardown", "sensitive", "vars",
}

# setup / teardown 里每一步允许出现的字段
ALLOWED_STEP_KEYS = {"name", "url", "method", "params", "headers", "timeout"}


class CaseError(Exception):
    """用例或配置写错了。cli 接住它，退出码 3。"""


@dataclass
class Loaded:
    """加载结果：用例 + 非致命警告（未知字段这类）。"""

    cases: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------- 配置

def merge(base, override):
    """环境配置覆盖基础配置，字典一层层合，别的类型直接顶掉。"""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(env=None, base_dir=None, overrides=None):
    """读 config/config.yaml，有环境就深合并 envs/<name>.yaml，CLI 覆盖最后生效。"""
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parents[1]
    config_file = base / "config" / "config.yaml"
    if not config_file.exists():
        raise CaseError(f"找不到配置文件：{config_file}")
    data = _read_yaml(config_file)
    if not isinstance(data, dict):
        raise CaseError(f"{config_file.name} 顶层要是字典")
    if env:
        env_file = base / "config" / "envs" / f"{env}.yaml"
        if not env_file.exists():
            raise CaseError(f"找不到环境配置：{env_file}")
        override = _read_yaml(env_file)
        if not isinstance(override, dict):
            raise CaseError(f"环境配置 {env_file.name} 顶层要是字典")
        data = merge(data, override)

    # 优先级：命令行 > 环境文件 > 基础配置
    if overrides:
        data = merge(data, overrides)

    data.setdefault("report_dir", "report")
    data.setdefault("retry_count", 3)
    data.setdefault("retry_backoff", 0)
    data.setdefault("timeout", 5)
    data.setdefault("archive_keep", DEFAULT_ARCHIVE_KEEP)
    data.setdefault("headers", {})
    data.setdefault("sensitive", list(masking.DEFAULT_SENSITIVE))

    if not data.get("base_url"):
        raise CaseError(f"{config_file.name} 缺少 base_url")
    if not isinstance(data["headers"], dict):
        raise CaseError(f"{config_file.name}：headers 要是字典")
    if not isinstance(data["sensitive"], list):
        raise CaseError(f"{config_file.name}：sensitive 要是列表")
    for key in ("retry_count", "archive_keep"):
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CaseError(f"{config_file.name}：{key} 要是非负整数")
    if data["retry_count"] < 1:
        raise CaseError(f"{config_file.name}：retry_count 至少是 1")
    if data["archive_keep"] < 1:
        raise CaseError(f"{config_file.name}：archive_keep 至少是 1")
    _validate_timeout(data["timeout"], f"{config_file.name}：")
    _validate_backoff(data["retry_backoff"], f"{config_file.name}：")
    return data


def _validate_backoff(value, prefix):
    """重试间隔：一个数字（固定）或一串数字（按次递增），单位秒。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, list)):
        raise CaseError(f"{prefix}retry_backoff 要是数字或非空数字列表")
    values = value if isinstance(value, list) else [value]
    if not values:
        raise CaseError(f"{prefix}retry_backoff 要是数字或非空数字列表")
    for item in values:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or item < 0:
            raise CaseError(f"{prefix}retry_backoff 的每一项都要是大于等于 0 的数字，现在是 {item!r}")


def _read_yaml(path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CaseError(f"{path.name} 不是合法 YAML：{exc}") from exc


# ---------------------------------------------------------------- 数据文件

def coerce(value):
    """CSV 里全是字符串，按内容转回 bool / int / float，转不了就保留原样。"""
    text = str(value).strip()
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def read_data_file(relative, base_dir=None):
    """读外部数据文件，返回一组字典。CSV 用表头当变量名，JSON 要求是对象数组。"""
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parents[1]
    path = Path(relative)
    if not path.is_absolute():
        path = base / path
    if not path.exists():
        raise CaseError(f"找不到数据文件：{path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as handle:
            return [{key: coerce(value) for key, value in row.items()}
                    for row in csv.DictReader(handle)]
    if suffix == ".json":
        with open(path, encoding="utf-8") as handle:
            rows = json.load(handle)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise CaseError(f"JSON 数据文件要写成对象数组：{path}")
        return rows
    raise CaseError(f"数据文件只认 .csv 和 .json：{path}")


def case_rows(case, base_dir=None):
    """取用例的数据行，data 和 data_file 二选一。"""
    if case.get("data") and case.get("data_file"):
        raise CaseError(f'{case.get("name")}：data 和 data_file 只能给一个')
    if case.get("data_file"):
        return read_data_file(case["data_file"], base_dir)
    return case.get("data")


# ---------------------------------------------------------------- 校验

def _where(label, case):
    name = case.get("name") if isinstance(case, dict) else None
    return f"{label}「{name}」" if name else label


def _check_url(spec, label):
    url = spec.get("url")
    if not isinstance(url, str) or not url.strip():
        raise CaseError(f"{label}：缺少 url")
    if not (url.startswith("/") or url.startswith("http://") or url.startswith("https://")):
        raise CaseError(f'{label}：url 要以 / 或 http(s):// 开头，现在是「{url}」')


def _check_method(spec, label):
    method = spec.get("method")
    if not isinstance(method, str):
        raise CaseError(f"{label}：缺少 method")
    if method.upper() not in METHODS:
        raise CaseError(f'{label}：method 只支持 {", ".join(METHODS)}，现在是「{method}」')


def _check_mapping(spec, label, key):
    value = spec.get(key)
    if value is not None and not isinstance(value, dict):
        raise CaseError(f"{label}：{key} 要是字典")


def _validate_timeout(value, prefix):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float, list)):
        raise CaseError(f"{prefix}timeout 要是数字或 [连接, 读取]")
    if isinstance(value, list):
        if len(value) != 2:
            raise CaseError(f"{prefix}timeout 写成 [连接秒数, 读取秒数]")
        for part in value:
            if isinstance(part, bool) or not isinstance(part, (int, float)) or part <= 0:
                raise CaseError(f"{prefix}timeout 的每一项都要是大于 0 的数字，现在是 {part!r}")
        return
    if value <= 0:
        raise CaseError(f"{prefix}timeout 要大于 0")


def _check_timeout(spec, label):
    _validate_timeout(spec.get("timeout"), f"{label}：")


def validate_step(step, label):
    """校验 setup / teardown 里的一步。"""
    if not isinstance(step, dict):
        raise CaseError(f"{label}：每一步要写成字典")
    if not isinstance(step.get("name"), str) or not step["name"].strip():
        raise CaseError(f"{label}：setup / teardown 的每一步都要有 name")
    where = _where(label, step)
    unknown = set(step) - ALLOWED_STEP_KEYS
    if unknown:
        raise CaseError(f'{where}：不认识的字段 {", ".join(sorted(unknown))}')
    _check_url(step, where)
    _check_method(step, where)
    _check_mapping(step, where, "params")
    _check_mapping(step, where, "headers")
    _check_timeout(step, where)


def validate_case(case, label, strict=False, warnings=None):
    """校验一条用例，写错了直接 CaseError，未知字段按 strict 处理。"""
    if not isinstance(case, dict):
        raise CaseError(f"{label}：一条用例要写成字典")

    if not isinstance(case.get("name"), str) or not case["name"].strip():
        raise CaseError(f"{label}：缺少 name")
    where = _where(label, case)

    unknown = set(case) - ALLOWED_KEYS
    if unknown:
        message = f'{where}：不认识的字段 {", ".join(sorted(unknown))}'
        if strict:
            raise CaseError(message)
        if warnings is not None:
            warnings.append(message)

    _check_url(case, where)
    _check_method(case, where)
    _check_mapping(case, where, "params")
    _check_mapping(case, where, "headers")
    _check_timeout(case, where)

    expect = case.get("expect")
    if not isinstance(expect, list) or not expect:
        raise CaseError(f"{where}：expect 要是非空列表")
    for number, item in enumerate(expect, 1):
        problem = assertion.validate_item(item)
        if problem:
            raise CaseError(f"{where}：第{number}条{problem}")

    tags = case.get("tags")
    if tags is not None and (not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags)):
        raise CaseError(f"{where}：tags 要是字符串列表")

    save = case.get("save")
    if save is not None and not isinstance(save, dict):
        raise CaseError(f"{where}：save 要写成 {变量名: 字段路径}")

    if "serial" in case and not isinstance(case["serial"], bool):
        raise CaseError(f"{where}：serial 要是 true / false")
    if "group" in case and not isinstance(case["group"], str):
        raise CaseError(f"{where}：group 要是字符串")
    if "sensitive" in case:
        sensitive = case["sensitive"]
        if not isinstance(sensitive, list) or not all(isinstance(item, str) for item in sensitive):
            raise CaseError(f"{where}：sensitive 要是字符串列表")
    if "vars" in case and not isinstance(case["vars"], dict):
        raise CaseError(f"{where}：vars 要是字典")

    depends_on = case.get("depends_on")
    if depends_on is not None:
        if isinstance(depends_on, str):
            depends_on = [depends_on]
        if not isinstance(depends_on, list) or not all(isinstance(item, str) and item for item in depends_on):
            raise CaseError(f"{where}：depends_on 要是用例名或用例名列表")
        case["depends_on"] = depends_on

    if case.get("data") is not None and not isinstance(case["data"], list):
        raise CaseError(f"{where}：data 要是列表")
    if case.get("data_file") is not None and not isinstance(case["data_file"], str):
        raise CaseError(f"{where}：data_file 要是字符串")
    if case.get("data") and case.get("data_file"):
        raise CaseError(f"{where}：data 和 data_file 只能给一个")

    for key in ("setup", "teardown"):
        steps = case.get(key)
        if steps is None:
            continue
        if not isinstance(steps, list):
            raise CaseError(f"{where}：{key} 要是列表")
        for step in steps:
            validate_step(step, where)


# ---------------------------------------------------------------- 展开与排序

def expand_cases(cases, base_dir=None):
    """带数据行的用例按行展开，每行单独算一条。

    用 deepcopy：展开出来的每条用例各自持有自己的 setup / teardown，
    否则 prepare 原地改一步的 url，会把同一份列表改第二遍。
    """
    expanded = []
    for case in cases:
        rows = case_rows(case, base_dir)
        if not rows:
            expanded.append(copy.deepcopy(case))
            continue
        for number, row in enumerate(rows, 1):
            item = copy.deepcopy(case)
            item.pop("data", None)
            item.pop("data_file", None)
            item["vars"] = dict(row)
            item["name"] = f'{case["name"]}[{number}]'
            expanded.append(item)
    return expanded


def order_by_deps(cases):
    """按 depends_on 做拓扑排序，顺带查重名、查缺失、查成环。"""
    by_name = {}
    for case in cases:
        deps = case.get("depends_on") or []
        if isinstance(deps, str):
            deps = [deps]
        case["depends_on"] = deps
        if case["name"] in by_name:
            raise CaseError(f'用例名重复：{case["name"]}')
        by_name[case["name"]] = case

    for case in cases:
        for dep in case["depends_on"]:
            if dep not in by_name:
                raise CaseError(f'{case["name"]} 依赖的用例不存在：{dep}')

    ordered = []
    state = {}

    def visit(case, path):
        name = case["name"]
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            raise CaseError("用例依赖成环：" + " -> ".join(path + [name]))
        state[name] = 1
        for dep in case["depends_on"]:
            visit(by_name[dep], path + [name])
        state[name] = 2
        ordered.append(case)

    for case in cases:
        visit(case, [])
    return ordered


def serial_reasons(case):
    """一条用例为什么必须走串行通道。空列表表示可以并发。"""
    reasons = []
    if case.get("serial"):
        reasons.append("显式 serial")
    if case.get("save"):
        reasons.append("save 变量")
    names = [name for _, name in variables.find_placeholders(case)
             if not name.startswith(variables.ENV_PREFIX)]
    if names:
        reasons.append(f"引用 ${{{names[0]}}}")
    return reasons


def mark_serial_chain(cases):
    """依赖链整条走串行通道，不然被依赖的用例可能排在并发通道里还没跑。"""
    reasons = {case["name"]: serial_reasons(case) for case in cases}
    serial = {name for name, why in reasons.items() if why}
    changed = True
    while changed:
        changed = False
        for case in cases:
            if case["name"] in serial:
                for dep in case.get("depends_on") or []:
                    if dep not in serial:
                        serial.add(dep)
                        reasons[dep].append("依赖链带上")
                        changed = True
    for case in cases:
        case["serial"] = case["name"] in serial
        case["serial_reasons"] = reasons[case["name"]]


# ---------------------------------------------------------------- 拼请求

def join_url(base_url, path):
    """把 base_url 和用例里的 url 拼起来。已经是完整地址就原样返回。"""
    if path.startswith(("http://", "https://")):
        return path
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def prepare_step(step, config, default_headers, default_timeout):
    prepared = dict(step)
    prepared["url"] = join_url(config["base_url"], step["url"])
    prepared["method"] = str(step.get("method") or "GET").upper()
    prepared["params"] = dict(step.get("params") or {})
    prepared["headers"] = {**(default_headers or {}), **(step.get("headers") or {})}
    prepared["timeout"] = step.get("timeout", default_timeout)
    return prepared


def prepare(case, config, default_headers, default_timeout):
    """返回加工好的新用例：拼完整 URL、合请求头、注入超时。

    不改入参，所以同一条用例被处理两次也不会把 URL 拼两遍。
    """
    prepared = dict(case)
    prepared["url"] = join_url(config["base_url"], case["url"])
    prepared["method"] = str(case.get("method") or "GET").upper()
    prepared["params"] = dict(case.get("params") or {})
    prepared["headers"] = {**(default_headers or {}), **(case.get("headers") or {})}
    prepared["timeout"] = case.get("timeout", default_timeout)
    prepared["setup"] = [prepare_step(step, config, default_headers, default_timeout)
                         for step in case.get("setup") or []]
    prepared["teardown"] = [prepare_step(step, config, default_headers, default_timeout)
                            for step in case.get("teardown") or []]
    return prepared


# ---------------------------------------------------------------- 入口

def load_cases(env=None, strict=False, base_dir=None, config=None, case_dir=None):
    """读全部用例，返回 Loaded(cases, warnings)。"""
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parents[1]
    config = config or load_config(env, base)
    directory = Path(case_dir) if case_dir else base / "case"
    if not directory.is_dir():
        raise CaseError(f"找不到用例目录：{directory}")

    warnings = []
    raw_cases = []
    for file in sorted(directory.rglob("*.yaml")):
        relative = file.relative_to(directory).as_posix()
        document = _read_yaml(file)
        if document is None:
            continue
        if not isinstance(document, list):
            raise CaseError(f"{relative} 顶层要是用例列表")
        for number, case in enumerate(document, 1):
            validate_case(case, f"{relative} 第{number}条", strict, warnings)
            raw_cases.append(case)

    cases = expand_cases(raw_cases, base)
    cases = order_by_deps(cases)
    mark_serial_chain(cases)

    default_headers = config.get("headers") or {}
    default_timeout = config.get("timeout", 5)
    prepared = []
    for index, case in enumerate(cases, 1):
        item = prepare(case, config, default_headers, default_timeout)
        item["index"] = index
        prepared.append(item)
    return Loaded(cases=prepared, warnings=warnings)
