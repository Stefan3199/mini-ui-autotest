import requests

from engine.case_loader import load_cases


def send_case(case):
    url = case["url"]     # 从 case 里取地址
    method = case["method"]   # 从 case 里取方法
    params = case["params"]   # 从 case 里取参数
    if method == "POST":
        r = requests.post(url, json=params)    # POST 的发法
    else:
        r = requests.get(url, params=params)   # GET 的发法
    return r.json()     # 交出去的东西，按你刚选的乙

if __name__ == "__main__":
    case = {"name": "自测", "url": "http://127.0.0.1:9300/login",
            "method": "POST", "params": {"user": "test001", "pwd": "right123"}}
    print(send_case(case))