import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]         # 常量一：项目根，用 __file__ 那套往上退两格
CONFIG_FILE = Path(__file__).resolve().parents[1] / "config" / "config.yaml"  # 常量二：项目根 接 config 接 config.yaml
CASE_DIR = Path(__file__).resolve().parents[1] / "case"  # 常量三：项目根 接 case 接 login.yaml


def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data             # 打开 CONFIG_FILE 读成字典，交出去


def load_cases():
    config = load_config()
    default_headers = config.get("headers", {})
    cases = []
    for file in sorted(CASE_DIR.glob("*.yaml")):
        with open(file, encoding="utf-8") as f:
            cases.extend(yaml.safe_load(f) or [])
    for case in cases:
        case["url"] = config["base_url"] + case["url"]
        case_headers = case.get("headers") or {}
        case["headers"] = {**default_headers, **case_headers}
    return cases


if __name__ == "__main__":
    print(load_cases())             # 把 load_cases() 的结果打出来