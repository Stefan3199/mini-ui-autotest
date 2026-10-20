"""被测 demo 服务：一个最小的登录接口，给 mini-ui-autotest 练手用。

启动：
    python demo/login_server.py

接口：
    POST http://127.0.0.1:9300/login
    请求体：{"user": "test001", "pwd": "right123"}
    成功：{"ok": true, "code": 0, "token": "TOK-abc-123"}
    失败：{"ok": false, "code": 1}
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

USERS = {"test001": "right123"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/login":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length).decode("utf-8"))

        if USERS.get(data.get("user")) == data.get("pwd"):
            reply = {"ok": True, "code": 0, "token": "TOK-abc-123"}
        else:
            reply = {"ok": False, "code": 1}

        payload = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


    def do_GET(self):
        if self.path != "/echo":
            self.send_response(404)
            self.end_headers()
            return
        reply = {"code": 0, "x_token": self.headers.get("X-Token", "")}
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
    HTTPServer(("127.0.0.1", 9300), Handler).serve_forever()
