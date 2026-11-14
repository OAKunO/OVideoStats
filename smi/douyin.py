#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音开放平台（open.douyin.com）客户端。

为什么走这条路：抖音网页端的视频详情和作品列表都被 Argus 签名挡住，
扫码登录页又是 bdms + secsdk + 验证码 的完整反爬栈，纯 Python 无头登录不可行
（见 probes/douyin.py 的实测）。但官方开放平台正好相反——所有接口都活着，
返回的是「access_token 过期」而不是「接口不存在」，只差一次授权。

代价：需要你先在 open.douyin.com 注册一个开发者应用，拿到 client_key 和
client_secret。换来的是一劳永逸的稳定，不用担心前端改版。

流程（标准 OAuth 2.0）：
    1. 拼授权链接 → 浏览器打开 → 你扫码登录并点授权
    2. 回调带回 code（本工具的 --listen 可自动接住，否则手动粘贴）
    3. code 换 access_token + refresh_token + open_id
    4. 用 access_token 调 /video/list/ 等接口

令牌有效期：access_token 约 15 天，refresh_token 约 30 天，过期前自动刷新。
"""

import json
import random
import string
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_SSL = ssl.create_default_context()
BASE = "https://open.douyin.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

CONFIG_FILE = Path("data/credentials/douyin.json")

# 常用错误码 → 人话
ERROR_HINTS = {
    2190008: "access_token 已过期，会自动尝试刷新；刷新也失败就重新授权",
    10008: "access_token 无效，需要重新授权",
    10007: "授权码已失效，请重新获取",
    2190002: "缺少权限：这个 scope 没有在应用里申请通过",
    21000001: "open_id 与 access_token 不匹配",
}


class DouyinError(Exception):
    """接口返回了非预期结果。"""


class NeedAuthorize(DouyinError):
    """授权失效，需要重新走一次 OAuth。"""


# --------------------------------------------------------------------------- #
# 应用配置与令牌
# --------------------------------------------------------------------------- #
def load_config(path=None):
    path = Path(path or CONFIG_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_config(config, path=None):
    path = Path(path or CONFIG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        import os
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def new_state():
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(16))


def authorize_url(client_key, redirect_uri, scopes, state):
    """拼授权链接。scope 用逗号分隔。"""
    query = urllib.parse.urlencode({
        "client_key": client_key,
        "response_type": "code",
        "scope": ",".join(scopes),
        "redirect_uri": redirect_uri,
        "state": state,
    })
    return "%s/platform/oauth/connect/?%s" % (BASE, query)


# --------------------------------------------------------------------------- #
# 底层请求
# --------------------------------------------------------------------------- #
def _get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json",
        "Referer": "https://open.douyin.com/", "Accept-Encoding": "identity",
    })
    try:
        with urllib.request.urlopen(req, timeout=20, context=_SSL) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            raise DouyinError("HTTP %s" % exc.code)
    except Exception as exc:  # noqa: BLE001
        raise DouyinError("请求失败：%r" % (exc,))


def _envelope(payload):
    """把 open.douyin.com 的两种响应包统一成 (error_code, description, data)。

    有的接口把 error_code 放在 data 里，有的放在顶层，这里都兜住。
    """
    data = payload.get("data")
    if isinstance(data, dict):
        code = data.get("error_code", payload.get("err_no", 0))
        desc = data.get("description") or payload.get("message") or payload.get("err_msg") or ""
        return code, desc, data
    code = payload.get("err_no", 0)
    desc = payload.get("err_msg") or payload.get("message") or ""
    return code, desc, data if isinstance(data, list) else {}


def _raise_if_error(code, desc):
    if code in (0, None):
        return
    hint = ERROR_HINTS.get(code, "")
    if code in (2190008, 10008, 10007):
        raise NeedAuthorize("抖音授权失效（error_code=%s %s）%s" % (code, desc, hint))
    raise DouyinError("抖音接口报错 error_code=%s %s %s" % (code, desc, hint))


# --------------------------------------------------------------------------- #
# OAuth
# --------------------------------------------------------------------------- #
def exchange_code(client_key, client_secret, code):
    query = urllib.parse.urlencode({
        "client_key": client_key, "client_secret": client_secret,
        "code": code, "grant_type": "authorization_code",
    })
    payload = _get("%s/oauth/access_token/?%s" % (BASE, query))
    code_, desc, data = _envelope(payload)
    _raise_if_error(code_, desc)
    if not data.get("access_token"):
        raise DouyinError("换 token 成功但没拿到 access_token：%s" % json.dumps(payload)[:200])
    return data


def refresh_access_token(client_key, refresh_token):
    query = urllib.parse.urlencode({
        "client_key": client_key, "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })
    payload = _get("%s/oauth/refresh_token/?%s" % (BASE, query))
    code, desc, data = _envelope(payload)
    _raise_if_error(code, desc)
    return data


def parse_redirect(text):
    """从用户粘贴的内容里取出 code。

    允许粘贴整个回调网址、只有 query 的片段、或者光秃秃的 code。
    真实场景里用户多半是把浏览器地址栏整条复制过来。
    """
    text = (text or "").strip().strip('"').strip("'")
    if not text:
        return None, None
    if "code=" in text:
        query = urllib.parse.urlparse(text).query or text.split("?", 1)[-1]
        params = dict(urllib.parse.parse_qsl(query))
        return params.get("code"), params.get("state")
    return text, None


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class DouyinClient:
    def __init__(self, config, auto_refresh=True):
        self.config = dict(config)
        self.auto_refresh = auto_refresh

    # -- 令牌 ------------------------------------------------------------- #
    @property
    def access_token(self):
        return self.config.get("access_token")

    @property
    def open_id(self):
        return self.config.get("open_id")

    def token_expired(self, margin=3600):
        expires_at = self.config.get("expires_at")
        if not expires_at:
            return False
        return time.time() > (expires_at - margin)

    def ensure_token(self):
        """快过期就先刷新，避免跑一半失效。"""
        if not self.access_token:
            raise NeedAuthorize("还没有授权。先运行：python3 -m smi dy-login")
        if not (self.auto_refresh and self.token_expired()):
            return False
        rt = self.config.get("refresh_token")
        if not rt:
            raise NeedAuthorize("access_token 将过期且没有 refresh_token，请重新授权")
        data = refresh_access_token(self.config["client_key"], rt)
        self._absorb_token(data)
        return True

    def _absorb_token(self, data):
        now = int(time.time())
        self.config["access_token"] = data.get("access_token")
        if data.get("refresh_token"):
            self.config["refresh_token"] = data["refresh_token"]
        if data.get("open_id"):
            self.config["open_id"] = data["open_id"]
        if data.get("expires_in"):
            self.config["expires_at"] = now + int(data["expires_in"])
        if data.get("refresh_expires_in"):
            self.config["refresh_expires_at"] = now + int(data["refresh_expires_in"])
        self.config["updated_at"] = now

    # -- 请求 ------------------------------------------------------------- #
    def _call(self, path, **params):
        """调接口；遇到令牌过期会自动刷新并重试一次。"""
        self.ensure_token()
        for attempt in range(2):
            query = urllib.parse.urlencode(
                dict(params, access_token=self.access_token, open_id=self.open_id))
            payload = _get("%s%s?%s" % (BASE, path, query))
            code, desc, data = _envelope(payload)
            try:
                _raise_if_error(code, desc)
            except NeedAuthorize:
                if attempt == 1 or not self.config.get("refresh_token"):
                    raise
                self._absorb_token(
                    refresh_access_token(self.config["client_key"],
                                         self.config["refresh_token"]))
                continue
            return data
        raise DouyinError("刷新令牌后仍然失败")

    # -- 数据接口 ---------------------------------------------------------- #
    def user_info(self):
        return self._call("/oauth/userinfo/")

    def video_list(self, cursor=0, count=20, max_pages=None, progress=None, exclude_pinned=False):
        """作品列表。分页直到 has_more 为假。

        返回 (videos, raw_total) —— videos 的字段与 B站 对齐，
        好让 export.py 直接复用。
        """
        videos = []
        page = 1
        while True:
            data = self._call("/video/list/", cursor=cursor, count=count,
                              exclude_pinned=str(bool(exclude_pinned)).lower())
            items = data.get("list") or []
            for it in items:
                videos.append(self._normalize(it))
            if progress:
                progress(page, len(videos))
            cursor = data.get("cursor", cursor)
            if not data.get("has_more"):
                break
            if max_pages and page >= max_pages:
                break
            if not items:
                break
            page += 1
            time.sleep(1.0)
        return videos, len(videos)

    def video_data(self, item_ids):
        """批量视频数据。item_ids 最多 20 个一批。"""
        out = []
        ids = list(item_ids)
        for i in range(0, len(ids), 20):
            batch = ids[i:i + 20]
            data = self._call("/video/data/", item_ids=",".join(batch))
            out.extend(data.get("list") or [])
            time.sleep(1.0)
        return out

    def fans_list(self, cursor=0, count=20):
        return self._call("/fans/list/", cursor=cursor, count=count)

    @staticmethod
    def _normalize(item):
        """把抖音的字段对齐成 B站 的形状，方便复用导出与统计。"""
        stat = item.get("statistics") or {}
        return {
            "bvid": item.get("item_id"),          # 复用同一列：平台内唯一 id
            "title": (item.get("title") or "").strip(),
            "created": item.get("create_time"),
            "play": stat.get("play_count"),
            "like": stat.get("digg_count"),
            "comment": stat.get("comment_count"),
            "share": stat.get("share_count"),
            "forward": stat.get("forward_count"),
            "is_top": item.get("is_top"),
            "length": None,
            "url": item.get("share_url"),
            "cover": item.get("cover"),
            "item_id": item.get("item_id"),
        }
