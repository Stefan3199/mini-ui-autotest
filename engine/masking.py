"""脱敏：参数和请求头进报告、JUnit、result.json 之前统一打码。

只用一份敏感词表，参数和请求头共用，避免出现「请求头打码了、参数没打」。
"""

# 默认敏感词，键名里包含这些片段就整值替换成 ***
DEFAULT_SENSITIVE = (
    "pwd",
    "pass",
    "token",
    "auth",
    "key",
    "secret",
    "cookie",
    "session",
)

MASK = "***"


def is_sensitive(key, extra=()):
    """键名是否敏感。大小写不敏感，子串匹配。"""
    low = str(key).lower()
    if low in {str(item).lower() for item in extra}:
        return True
    return any(word in low for word in DEFAULT_SENSITIVE)


def mask(mapping, extra=()):
    """返回打码后的新字典，嵌套字典和列表里的字典都走一遍。"""
    if not mapping:
        return {}
    return {key: (MASK if is_sensitive(key, extra) else _mask_value(value, extra))
            for key, value in mapping.items()}


def _mask_value(value, extra):
    if isinstance(value, dict):
        return mask(value, extra)
    if isinstance(value, list):
        return [_mask_value(item, extra) for item in value]
    return value
