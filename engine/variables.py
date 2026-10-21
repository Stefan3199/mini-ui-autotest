"""变量替换：把用例里的 ${name} 换成上下文里的值。"""

import re

PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


class MissingVar(Exception):
    """用例里写了 ${name}，但上下文里没有这个变量。"""


def _lookup(match, context):
    name = match.group(1)
    if name not in context:
        raise MissingVar(name)
    return str(context[name])


def render(obj, context):
    """递归替换 ${name}。

    整个字符串就是一个变量时保留原类型，${want} 是 0 就还回 0；
    夹在文字中间时转成字符串，比如 "Bearer ${token}"。
    变量没定义直接报错，不吃默认值。
    """
    if isinstance(obj, str):
        whole = PLACEHOLDER.fullmatch(obj)
        if whole:
            name = whole.group(1)
            if name not in context:
                raise MissingVar(name)
            return context[name]
        return PLACEHOLDER.sub(lambda m: _lookup(m, context), obj)
    if isinstance(obj, dict):
        return {k: render(v, context) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render(v, context) for v in obj]
    return obj