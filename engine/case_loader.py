import csv
import json
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]         # 常量一：项目根，用 __file__ 那套往上退两格
CONFIG_FILE = Path(__file__).resolve().parents[1] / "config" / "config.yaml"  # 常量二：项目根 接 config 接 config.yaml
ENV_DIR = Path(__file__).resolve().parents[1] / "config" / "envs"  # 常量三：环境配置目录
CASE_DIR = Path(__file__).resolve().parents[1] / "case"  # 常量四：项目根 接 case 接 login.yaml


def merge(base, override):
    """环境配置覆盖基础配置，字典一层层合，别的类型直接顶掉。"""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(env=None):
    with open(CONFIG_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if env:
        env_file = ENV_DIR / f"{env}.yaml"
        if not env_file.exists():
            raise SystemExit(f"找不到环境配置：{env_file}")
        with open(env_file, encoding="utf-8") as f:
            data = merge(data, yaml.safe_load(f) or {})
    return data             # 打开 CONFIG_FILE 读成字典，有环境就合上去


def prepare(case, config, default_headers, default_timeout):
    """把一条请求加工成能直接发的样子：拼 URL、合请求头、注入超时。"""
    case["url"] = config["base_url"] + case["url"]
    case_headers = case.get("headers") or {}
    case["headers"] = {**default_headers, **case_headers}
    case["timeout"] = case.get("timeout", default_timeout)
    return case


def needs_order(case):
    """有 save 或引用了 ${} 的用例算有依赖，并发时要串行跑。"""
    if case.get("save"):
        return True
    return "${" in json.dumps(case, ensure_ascii=False)


def coerce(value):
    """CSV 里全是字符串，按内容转回 bool / int / float，转不了就保留原样。"""
    text = value.strip()
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
        return value


def read_data_file(relative):
    """读外部数据文件，返回一组字典。CSV 用表头当变量名，JSON 要求是对象数组。"""
    path = ROOT / relative
    if not path.exists():
        raise SystemExit(f"找不到数据文件：{path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            return [{key: coerce(value) for key, value in row.items()} for row in csv.DictReader(f)]
    if suffix == ".json":
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        if not isinstance(rows, list):
            raise SystemExit(f"JSON 数据文件要写成对象数组：{path}")
        return rows
    raise SystemExit(f"数据文件只认 .csv 和 .json：{path}")


def case_rows(case):
    """取用例的数据行，data 和 data_file 二选一。"""
    if case.get("data") and case.get("data_file"):
        raise SystemExit(f'{case["name"]}：data 和 data_file 只能给一个')
    if case.get("data_file"):
        return read_data_file(case["data_file"])
    return case.get("data")


def load_cases(env=None):
    config = load_config(env)
    default_headers = config.get("headers", {})
    default_timeout = config.get("timeout", 5)
    cases = []
    for file in sorted(CASE_DIR.glob("*.yaml")):
        with open(file, encoding="utf-8") as f:
            cases.extend(yaml.safe_load(f) or [])
    cases = expand_cases(cases)
    cases = order_by_deps(cases)
    mark_serial_chain(cases)
    for index, case in enumerate(cases, 1):
        prepare(case, config, default_headers, default_timeout)
        for step in case.get("setup", []) + case.get("teardown", []):
            prepare(step, config, default_headers, default_timeout)
        case["index"] = index
        case["serial"] = case["serial"] or needs_order(case)
    return cases


def expand_cases(cases):
    """带数据行的用例按行展开，每行单独算一条。"""
    expanded = []
    for case in cases:
        rows = case_rows(case)
        if not rows:
            expanded.append(case)
            continue
        for index, row in enumerate(rows, 1):
            item = dict(case)
            item.pop("data", None)
            item.pop("data_file", None)
            item["vars"] = row
            item["name"] = f'{case["name"]}[{index}]'
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
            raise SystemExit(f'用例名重复：{case["name"]}')
        by_name[case["name"]] = case

    for case in cases:
        for dep in case["depends_on"]:
            if dep not in by_name:
                raise SystemExit(f'{case["name"]} 依赖的用例不存在：{dep}')

    ordered = []
    state = {}

    def visit(case, path):
        name = case["name"]
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            raise SystemExit("用例依赖成环：" + " -> ".join(path + [name]))
        state[name] = 1
        for dep in case["depends_on"]:
            visit(by_name[dep], path + [name])
        state[name] = 2
        ordered.append(case)

    for case in cases:
        visit(case, [])
    return ordered


def mark_serial_chain(cases):
    """依赖链整条走串行通道，不然被依赖的用例可能排在并发通道里还没跑。"""
    serial = {case["name"] for case in cases if case.get("serial") or case.get("depends_on")}
    changed = True
    while changed:
        changed = False
        for case in cases:
            if case["name"] in serial:
                for dep in case["depends_on"]:
                    if dep not in serial:
                        serial.add(dep)
                        changed = True
    for case in cases:
        case["serial"] = case["name"] in serial

if __name__ == "__main__":
    print(load_cases())             # 把 load_cases() 的结果打出来
