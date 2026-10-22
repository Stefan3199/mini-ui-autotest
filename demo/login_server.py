"""被测 demo 服务：给 mini-ui-autotest 练手用的最小接口集合。

启动：
    python demo/login_server.py          # 默认 127.0.0.1:9300
    python demo/login_server.py 9400     # 换端口，并发跑测试时有用

接口：
    POST /login                 请求体 {"user","pwd"}，成功 {"ok":true,"code":0,"token":"TOK-abc-123"}
    POST /admin/user            建临时用户
    POST /admin/user/delete     删临时用户
    GET  /admin/user/get?user=  查临时用户是否在
    GET  /order                 嵌套结构：data.user / data.items / data.total
    GET  /user                  断言用：name / roles / age
    GET  /echo                  回显 X-Token
    GET  /slow                  睡 3 秒，试超时和并发
    GET  /flip                  第一次失败之后成功，试 --rerun
    GET  /html                  返回 HTML 而不是 JSON，试「响应不是 JSON」
    GET  /broken                返回半截 JSON，试解析失败
    GET  /notfound              404 + JSON body
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

USERS = {"test001": "right123"}
TEMP_USERS = set()
FLIP = {"count": 0}


def _reply(handler, status, payload, content_type="application/json; charset=utf-8"):
    body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}

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

        _reply(self, 200, reply)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/notfound":
            _reply(self, 404, {"code": 404, "msg": "not found"})
            return
        if path == "/html":
            # 故意返回 HTML：用来看「响应不是 JSON」怎么判
            _reply(self, 200, "<html><body><h1>502 Bad Gateway</h1></body></html>",
                   "text/html; charset=utf-8")
            return
        if path == "/broken":
            _reply(self, 200, '{"code": 0, "msg": ', "application/json; charset=utf-8")
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
        _reply(self, 200, reply)

    def log_message(self, format, *args):
        print("[demo]", format % args)


def serve(port=9300):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9300
    print(f"demo 服务已启动: http://127.0.0.1:{port}")
    serve(port).serve_forever()
