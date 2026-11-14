#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""各平台响应解析的公共助手。

抖音/视频号/小红书的响应结构各不相同，但「拆信封 → 找数组 → 按候选键取字段」
这套动作完全共通。原先在 channels_browser / xhs_browser / douyin_creator 里
各写了一份——同一件事写三遍，改一处忘两处。这里统一。

设计原则：**字段名没公开文档，所以一律按候选键容错**。宁可多试几个名字，
也不要因为平台改了个拼写就整个采不到数据。
"""

import json
import time

# 常见的信封键：{code, data: {...}} / {success, result: {...}}
ENVELOPE_KEYS = ("data", "result")
# 常见的「数组」字段名
LIST_KEYS = ("notes", "note_list", "noteList", "list", "post_list", "object_list",
             "objectList", "feeds", "posts", "items", "records", "archives", "vlist")
# 判断「这看起来像作品数组」用的 id 字段名
ID_KEYS = ("id", "noteId", "note_id", "noteIdStr", "objectId", "object_id",
           "exportId", "export_id", "aweme_id", "item_id", "contentId", "bvid")
# 统计值可能挂在这些子对象下
STAT_CONTAINERS = ("statistics", "stat", "stats", "metrics", "postStat", "data", None)


# --------------------------------------------------------------------------- #
# 基本取值
# --------------------------------------------------------------------------- #
def pick(obj, *keys, default=None):
    """按顺序取第一个「有值」的键。

    空字符串、空列表、空字典都算没值——接口经常用它们表示「没有」。
    """
    if not isinstance(obj, dict):
        return default
    for key in keys:
        value = obj.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def to_number(value):
    """转成数值。支持 '1.2万' / '3,456' / '12.5%' / 毫秒时间戳以外的数值。

    **整数值保持 int，小数值保持 float** —— 点击率、完播率这类是小数，
    一律转 int 会直接归零，是静默的数据错误（这个坑真踩过）。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else float(value)

    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text or text in ("-", "--", "—", "暂无"):
        return None

    percent = text.endswith("%")
    if percent:
        text = text[:-1]
    factor = 1
    for suffix, mult in (("亿", 100000000), ("万", 10000)):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
            factor = mult
            break
    try:
        number = float(text) * factor
    except ValueError:
        return None
    if percent:
        return number / 100.0
    return int(number) if number.is_integer() else number


def to_ts(value):
    """转成 Unix 秒。毫秒自动降级；常见日期字符串也认。认不出就原样返回。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = int(value)
        return stamp // 1000 if stamp > 10 ** 12 else stamp
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return int(time.mktime(time.strptime(text, fmt)))
        except ValueError:
            continue
    return value


def as_text(value):
    """把可能是结构化对象的文本字段取成字符串。

    实测抖音和小红书的标题字段有时是 dict（例如 `{'title': '...'}`），
    直接 `.strip()` 会抛 AttributeError。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("title", "text", "content", "desc", "description",
                    "display_title", "plainText", "name"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
        return ""
    if isinstance(value, (list, tuple)):
        for entry in value:
            text = as_text(entry)
            if text:
                return text
    return ""


# --------------------------------------------------------------------------- #
# 拆信封与找数组
# --------------------------------------------------------------------------- #
def unwrap(payload, keys=ENVELOPE_KEYS, depth=2):
    """剥掉 {code, data:{result:{...}}} 这类信封，尽量返回最里层。

    只在「值是 dict」时才往里走，所以对 `data: {list: [...]}` 会原样停在 data，
    不会误伤数组。
    """
    current = payload
    for _ in range(depth):
        if not isinstance(current, dict):
            break
        nxt = None
        for key in keys:
            value = current.get(key)
            if isinstance(value, dict):
                nxt = value
                break
        if nxt is None:
            break
        current = nxt
    return current if isinstance(current, dict) else (payload if isinstance(payload, dict) else {})


def find_list(payload, keys=LIST_KEYS, id_keys=ID_KEYS, max_depth=4):
    """在响应里递归找出「作品数组」。

    两步走：先按已知字段名找；找不到再退一步，找「元素带 id 字段」的数组。
    这样平台换个字段名也不至于直接采不到。
    """
    fallback = []

    def walk(node, depth):
        if depth > max_depth:
            return None
        if isinstance(node, dict):
            for key in keys:
                value = node.get(key)
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    return value
            for value in node.values():
                if isinstance(value, dict):
                    hit = walk(value, depth + 1)
                    if hit:
                        return hit
                elif isinstance(value, list) and value and isinstance(value[0], dict):
                    if set(value[0]) & set(id_keys) and not fallback:
                        fallback.append(value)
        elif isinstance(node, list) and node and isinstance(node[0], dict):
            if set(node[0]) & set(id_keys) and not fallback:
                fallback.append(node)
        return None

    ordered = []
    for key in keys:          # 先扫顶层
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, list) and value:
            ordered = value
            break
    if ordered:
        return ordered
    return walk(payload, 0) or (fallback[0] if fallback else [])


def metric(item, keys, containers=STAT_CONTAINERS):
    """按候选键在多个可能的层级里找统计值。

    统计数据可能平铺在顶层，也可能挂在 statistics / stats / data 下面，
    这里逐层兜底。
    """
    for container in containers:
        scope = item if container is None else item.get(container)
        if not isinstance(scope, dict):
            continue
        value = pick(scope, *keys)
        if value is not None:
            return to_number(value)
    return None


def cell(value):
    """Excel 只吃得下基本类型。字段偶尔是 dict/list，转成字符串而不是让导出崩掉。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)[:200]
    return str(value)
