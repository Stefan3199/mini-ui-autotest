import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]         # 常量一：项目根，用 __file__ 那套往上退两格
CONFIG_FILE = Path(__file__).resolve().parents[1] / "config" / "config.yaml"  # 常量二：项目根 接 config 接 config.yaml
CASE_FILE = Path(__file__).resolve().parents[1] / "case" / "login.yaml"   # 常量三：项目根 接 case 接 login.yaml


def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data             # 打开 CONFIG_FILE 读成字典，交出去


def load_cases():
    config = load_config()
    with open(CASE_FILE, encoding="utf-8") as f:
        cases = yaml.safe_load(f)
    for case in cases:
        case["url"] = config["base_url"] + case["url"]
    return cases             # 先拿 base_url，再读 CASE_FILE，逐条把地址接成完整的，交出整张表


if __name__ == "__main__":
    print(load_cases())             # 把 load_cases() 的结果打出来