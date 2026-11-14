#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""格式化工具。

此前 display_width/pad 在 bili.py、_fmt_ts 在 __main__ 和 export.py 里各有一份，
同一件事写了三遍。统一到这里。
"""

import time
import unicodedata


def display_width(text):
    """按终端列宽计算长度：中日韩全角字符算 2 列，其余算 1 列。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(text))


def pad(text, width):
    """左对齐补空格到指定显示宽度。"""
    text = str(text)
    return text + " " * max(0, width - display_width(text))


def fmt_ts(ts, default="-"):
    """时间戳 → YYYY-MM-DD。CSV 里用 default="" 避免出现 '-' 这种非空占位。"""
    if not ts:
        return default
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def rate_value(like, play):
    """点赞率的小数值，算不出时返回 None。

    Excel 需要的是数值 + 百分比格式，终端需要的是字符串，所以拆成两个。
    """
    if not play or like is None:
        return None
    return like / play


def fmt_rate(like, play):
    """点赞率的显示文本。算不出时返回 '-'，不硬凑一个数出来。"""
    value = rate_value(like, play)
    return "-" if value is None else "%.2f%%" % (value * 100)


def fmt_int(value, default="-"):
    """千分位整数。"""
    return "{:,}".format(value) if value is not None else default
