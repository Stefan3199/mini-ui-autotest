"""请求发送层的单元测试，用本地 stub 服务，不出网。"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.helpers import ROOT  # noqa: F401

from engine.request_sender import ProtocolError, TransportError, send_case


class Stub(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/json"):
            self.reply(200, json.dumps({"code": 0, "x_token": self.headers.get("X-Token", "")}))
        elif self.path.startswith("/empty"):
            self.reply(404, "")
        elif self.path.startswith("/html"):
            self.reply(200, "<html>502</html>", "text/html; charset=utf-8")
        elif self.path.startswith("/broken"):
            self.reply(200, '{"code": 0,')
        elif self.path.startswith("/slow"):
            import time
            time.sleep(0.5)
            self.reply(200, json.dumps({"code": 0}))
        else:
            self.reply(404, "")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        self.reply(200, json.dumps({"code": 0, "echo": json.loads(raw)}))

    # PUT 走同一套逻辑，用来验「不只有 GET / POST」
    do_PUT = do_POST

    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class SenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_json_ok(self):
        response = send_case({"url": self.url("/json"), "method": "GET",
                              "params": {}, "headers": {"X-Token": "abc"}, "timeout": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body["x_token"], "abc")
        self.assertEqual(response.body_kind, "json")
        self.assertGreaterEqual(response.elapsed_ms, 0)

    def test_post_sends_json_body(self):
        response = send_case({"url": self.url("/post"), "method": "POST",
                              "params": {"user": "test001"}, "timeout": 3})
        self.assertEqual(response.body["echo"], {"user": "test001"})

    def test_put_is_supported(self):
        response = send_case({"url": self.url("/put"), "method": "PUT",
                              "params": {"a": 1}, "timeout": 3})
        self.assertEqual(response.status_code, 200)

    def test_empty_body_is_protocol_error(self):
        with self.assertRaises(ProtocolError) as ctx:
            send_case({"url": self.url("/empty"), "method": "GET", "timeout": 3})
        error = ctx.exception
        self.assertEqual(error.kind, "json")
        self.assertEqual(error.status_code, 404)
        self.assertIn("不是 JSON", error.message)

    def test_html_body_is_protocol_error(self):
        with self.assertRaises(ProtocolError) as ctx:
            send_case({"url": self.url("/html"), "method": "GET", "timeout": 3})
        self.assertEqual(ctx.exception.status_code, 200)

    def test_broken_json_is_protocol_error(self):
        with self.assertRaises(ProtocolError):
            send_case({"url": self.url("/broken"), "method": "GET", "timeout": 3})

    def test_timeout_is_transport_error(self):
        with self.assertRaises(TransportError) as ctx:
            send_case({"url": self.url("/slow"), "method": "GET", "timeout": 0.05})
        self.assertEqual(ctx.exception.kind, "timeout")

    def test_connection_refused_is_transport_error(self):
        # 先占一个端口再放掉，保证这个端口没人监听
        import socket
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        with self.assertRaises(TransportError) as ctx:
            send_case({"url": f"http://127.0.0.1:{port}/x", "method": "GET", "timeout": 2})
        self.assertEqual(ctx.exception.kind, "connect")


if __name__ == "__main__":
    unittest.main()
