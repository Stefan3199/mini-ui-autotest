"""被测 demo 服务：一个最小的登录接口，给 mini-ui-autotest 练手用。

启动：
    python demo/login_server.py

接口：
    POST http://127.0.0.1:9300/login
    请求体：{"user": "test001", "pwd": "right123"}
    成功：{"ok": true, "code": 0, "token": "TOK-abc-123"}
    失败：{"ok": false, "code": 1}
"""

import time,json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

USERS = {"test001": "right123"}
TEMP_USERS = set()
FLIP = {"count": 0}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(raw)

        if self.path == "/login":
            if USERS.get(data.get("user")) == data.get("pwd"):
                reply = {"ok": True, "code": 0, "token": "TOK-abc-123"}
            else:
                reply = {"ok": False, "code": 1}
        elif self.path == "/admin/user":
            TEMP_USERS.add(data.get("user"))
            reply = {"code": 0, "exists": data.get("user") in TEMP_USERS}
        elif self.path == "/admin/user/delete":
            TEMP_USERS.discard(data.get("user"))
            reply = {"code": 0, "exists": data.get("user") in TEMP_USERS}
        else:
            self.send_response(404)
            self.end_headers()
            return

        payload = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/notfound":
            payload = json.dumps({"code": 404, "msg": "not found"}).encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/slow":
            time.sleep(3)
            reply = {"code": 0}
        elif path == "/flip":
            # 第一次失败，之后成功，用来试 --rerun
            FLIP["count"] += 1
            reply = {"code": 0 if FLIP["count"] > 1 else 1}
        elif path == "/admin/user/get":
            reply = {"code": 0, "exists": query.get("user", [""])[0] in TEMP_USERS}
        elif path == "/order":
            reply = {
                "code": 0,
                "data": {
                    "user": {"id": 7, "name": "test001"},
                    "items": [{"sku": "A1"}, {"sku": "B2"}],
                    "total": 99.5,
                },
            }
        elif path == "/echo":
            reply = {"code": 0, "x_token": self.headers.get("X-Token", "")}
        elif path == "/user":
            reply = {"code": 0, "name": "test001", "roles": ["admin", "tester"], "age": 30}
        else:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, format, *args):
        print("[demo]", format % args)


if __name__ == "__main__":
    print("demo 登录服务已启动: http://127.0.0.1:9300/login")
    ThreadingHTTPServer(("127.0.0.1", 9300), Handler).serve_forever()
