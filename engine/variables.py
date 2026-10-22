"""变量替换：把用例里的 ${name} 换成上下文里的值，${ENV:NAME} 换成环境变量。

顺带提供 find_placeholders，调度层用它判断一条用例有没有跨用例依赖
（只用 ${ENV:...} 不算依赖，不需要串行）。
"""

import os
import re

PLACEHOLDER = re.compile(r"\$\{((?:ENV:)?[A-Za-z_]\w*)\}")

ENV_PREFIX = "ENV:"


class MissingVar(Exception):
    """用例里写了 ${name}，但上下文里没有这个变量。"""

    def __init__(self, name):
        super().__init__(name)
        self.name = name


def _lookup(name, context, environ):
    if name.startswith(ENV_PREFIX):
        key = name[len(ENV_PREFIX):]
        if key not in environ:
            raise MissingVar(name)
        return environ[key]
    if name not in context:
        raise MissingVar(name)
    return context[name]


def render(obj, context, environ=None):
    """递归替换 ${name}。

    整个字符串就是一个变量时保留原类型，${want} 是 0 就还回 0；
    夹在文字中间时转成字符串，比如 "Bearer ${token}"。
    变量没定义直接报错，不吃默认值。${ENV:NAME} 从环境变量取。
    """
    if environ is None:
        environ = os.environ
    if isinstance(obj, str):
        whole = PLACEHOLDER.fullmatch(obj)
        if whole:
            return _lookup(whole.group(1), context, environ)
        return PLACEHOLDER.sub(lambda m: str(_lookup(m.group(1), context, environ)), obj)
    if isinstance(obj, dict):
        return {k: render(v, context, environ) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render(v, context, environ) for v in obj]
    return obj


def find_placeholders(obj, path=""):
    """找出对象里所有 ${} 引用，返回 ((字段路径, 变量名), ...)。

    路径用来在报告和报错里说清楚是哪一行的变量，比如 params.token。
    """
    found = []
    if isinstance(obj, str):
        for match in PLACEHOLDER.finditer(obj):
            found.append((path or "<值>", match.group(1)))
    elif isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            found.extend(find_placeholders(value, child))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            child = f"{path}.{index}" if path else str(index)
            found.extend(find_placeholders(value, child))
    return found
