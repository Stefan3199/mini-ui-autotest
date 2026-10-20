import requests

def send_case(case):
    url = case["url"]
    method = case["method"]
    params = case["params"]
    try:
        if method == "POST":
            r = requests.post(url, json=params,headers = case.get("headers", {}),timeout=case.get("timeout", 5))

        else:
            r = requests.get(url, params=params,headers = case.get("headers", {}),timeout=case.get("timeout", 5))

        return {"status_code": r.status_code, "body": r.json()}
    except requests.exceptions.RequestException:
        return None

if __name__ == "__main__":
    case = {"name": "自测", "url": "http://127.0.0.1:9300/login",
            "method": "POST", "params": {"user": "test001", "pwd": "right123"}}
    print(send_case(case))