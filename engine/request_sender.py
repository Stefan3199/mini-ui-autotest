"""发请求：把「传输错误」和「协议错误」分开，判决层才分得清。

- TransportError：连接被拒、超时、TLS 出错 —— 环境问题，可以重试
- ProtocolError：拿到了响应，但 body 不是 JSON —— 接口问题，不重试

只 catch 传输异常。非 JSON 响应曾经因为 requests 的 JSONDecodeError
继承自 RequestException 而被一起吞掉，结果被记成「环境抖动」。
"""

from dataclasses import dataclass
import time

import requests

# 支持的方法。GET 类把参数拼在 query，其余用 JSON body。
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
BODY_METHODS = ("POST", "PUT", "PATCH")

# 响应文本最多留这么多，进报告够用，不至于把报告撑大
MAX_TEXT = 2000


class TransportError(Exception):
    """没拿到可用的响应：连接失败、超时、其他传输层异常。"""

    def __init__(self, kind, message, elapsed_ms=0.0):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.elapsed_ms = elapsed_ms


class ProtocolError(Exception):
    """拿到了响应，但内容不是用例能断言的 JSON。"""

    def __init__(self, kind, message, status_code=None, text="", elapsed_ms=0.0):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status_code = status_code
        self.text = text
        self.elapsed_ms = elapsed_ms


@dataclass
class Response:
    """一次可用的响应。body 是解析后的 JSON，text 是原始文本。"""

    status_code: int
    body: object
    text: str = ""
    elapsed_ms: float = 0.0
    content_type: str = ""
    body_kind: str = "json"


def send_case(case, session=None):
    """发一条请求。成功返回 Response，失败抛 TransportError / ProtocolError。"""
    method = str(case.get("method") or "GET").upper()
    url = case["url"]
    params = case.get("params") or {}
    headers = case.get("headers") or {}
    timeout = case.get("timeout", 5)
    client = session or requests

    start = time.perf_counter()
    try:
        if method in BODY_METHODS:
            resp = client.request(method, url, json=params, headers=headers, timeout=timeout)
        else:
            resp = client.request(method, url, params=params, headers=headers, timeout=timeout)
    except requests.exceptions.ConnectTimeout as exc:
        # TCP 都没连上，是「服务不在」，不是「服务慢」
        raise TransportError("connect", f"连接超时：{exc}", _ms(start)) from exc
    except requests.exceptions.Timeout as exc:
        raise TransportError("timeout", f"{timeout} 秒内没拿到响应：{exc}",
                             _ms(start)) from exc
    except requests.exceptions.ConnectionError as exc:
        raise TransportError("connect", f"连接不上：{exc}", _ms(start)) from exc
    except requests.exceptions.RequestException as exc:
        raise TransportError("other", f"发送失败：{exc}", _ms(start)) from exc

    elapsed = _ms(start)
    content_type = resp.headers.get("Content-Type", "")
    try:
        text = resp.text
    except Exception:  # 极端编码问题，不该拖垮整轮
        text = ""
    try:
        body = resp.json()
    except ValueError as exc:
        raise ProtocolError(
            "json",
            f"响应不是 JSON（HTTP {resp.status_code}，Content-Type: {content_type or '无'}）",
            status_code=resp.status_code,
            text=text[:MAX_TEXT],
            elapsed_ms=elapsed,
        ) from exc

    if body is None:
        body_kind = "empty"
    elif isinstance(body, (dict, list)):
        body_kind = "json"
    else:
        body_kind = "scalar"
    return Response(status_code=resp.status_code, body=body, text=text[:MAX_TEXT],
                    elapsed_ms=elapsed, content_type=content_type, body_kind=body_kind)


def _ms(start):
    return round((time.perf_counter() - start) * 1000, 1)
