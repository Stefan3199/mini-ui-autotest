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


def load_cases(env=None):
    config = load_config(env)
    default_headers = config.get("headers", {})
    default_timeout = config.get("timeout", 5)
    cases = []
    for file in sorted(CASE_DIR.glob("*.yaml")):
        with open(file, encoding="utf-8") as f:
            cases.extend(yaml.safe_load(f) or [])
    cases = expand_cases(cases)
    for index, case in enumerate(cases, 1):
        prepare(case, config, default_headers, default_timeout)
        for step in case.get("setup", []) + case.get("teardown", []):
            prepare(step, config, default_headers, default_timeout)
        case["index"] = index
        case["serial"] = needs_order(case)
    return cases


def expand_cases(cases):
    """带 data 列表的用例按行展开，每行单独算一条。"""
    expanded = []
    for case in cases:
        rows = case.get("data")
        if not rows:
            expanded.append(case)
            continue
        for index, row in enumerate(rows, 1):
            item = dict(case)
            item.pop("data")
            item["vars"] = row
            item["name"] = f'{case["name"]}[{index}]'
            expanded.append(item)
    return expanded


if __name__ == "__main__":
    print(load_cases())             # 把 load_cases() 的结果打出来