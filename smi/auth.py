#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录与凭据管理 —— 为「部署到服务器」这个场景设计。

核心判断：**服务器上不需要浏览器，也不需要从本机 Chrome 里抠 Cookie。**
B站提供扫码登录接口，整个流程是无头的：

    generate → 拿到 qrcode_key 和二维码内容
    手机B站 App 扫一下并确认
    poll     → B站通过 Set-Cookie 下发 SESSDATA / bili_jct / DedeUserID

凭据落在 data/credentials/bilibili.json，权限 600，不进版本库。

和「匿名抓陌生人」的区别：
  * 匿名方案要绕开风控，覆盖率看账号名是否独特（95% 上下）
  * 自己登录后走 space/wbi/arc/search，覆盖率 100%，而且能拿到更多字段

因此对自己用的场景，**登录态不是负担，反而是更简单更完整的那条路**。
"""

import http.cookiejar
import json
import os
import random
import shutil
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_SSL = ssl.create_default_context()
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

CREDENTIAL_FILE = Path("data/credentials/bilibili.json")

# poll 返回码的官方约定
POLL_CODES = {
    0: "登录成功",
    86038: "二维码已失效",
    86090: "已扫码，等待手机端确认",
    86101: "尚未扫码",
}


class LoginError(Exception):
    """登录流程失败。"""


class LoginExpired(Exception):
    """本地凭据已失效，需要重新登录。"""


# --------------------------------------------------------------------------- #
# 凭据读写
# --------------------------------------------------------------------------- #
def save_credentials(cookie, mid=None, name=None, path=None):
    """写入凭据文件，并尽量收紧权限（服务器上可能是多用户环境）。"""
    path = Path(path or CREDENTIAL_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cookie": cookie,
        "mid": mid,
        "name": name,
        "login_at": int(time.time()),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def load_credentials(path=None, strict=False):
    """返回凭据 dict；不存在或损坏则返回 None。"""
    path = Path(path or CREDENTIAL_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        if strict:
            raise
        return None
    if isinstance(data, dict) and data.get("cookie"):
        return data
    return None


def clear_credentials(path=None):
    path = Path(path or CREDENTIAL_FILE)
    try:
        path.unlink()
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# 二维码渲染
# --------------------------------------------------------------------------- #
def _qr_matrix(text, border=2):
    """生成二维码点阵。返回 (matrix, None) 或 (None, 错误说明)。

    纠错等级 M（15%）：从屏幕上扫，要扛得住反光和缩放。
    border 是静默区宽度：终端里省地方用 2，导出成图片时用规范要求的 4。
    """
    try:
        import qrcode
    except ImportError:
        return None, "未安装 qrcode 库"
    qr = qrcode.QRCode(border=border,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.get_matrix(), None


def render_qr_terminal(text, columns=None):
    """把二维码渲染成终端字符画，**按终端宽度自适应**。

    这里踩过一个真实的坑：B站登录二维码内容是 131 个字符，点阵 51×51。
    若每个模块占两个字符（最常见的画法），需要 102 字符宽，而标准终端只有
    80 列 —— 一旦自动换行，二维码就彻底报废，还看不出来。

    所以先量终端宽度：
      * 放得下 → 全块渲染（██，每个模块 2 字符宽 × 1 字符高，靠宽度补偿
        终端字符约 1:2 的高宽比，最清晰）
      * 放不下 → 半块渲染（一个字符承载上下两个模块），宽度直接减半
      * 还放不下 → 明确报错，让调用方改用 --qr-out 导出图片
    """
    matrix, err = _qr_matrix(text)
    if err:
        return None, err
    n = len(matrix)
    cols = columns or shutil.get_terminal_size((100, 24)).columns

    if n * 2 + 2 <= cols:
        art = "\n".join("".join("██" if c else "  " for c in row) for row in matrix)
        return art, "full"

    if n + 2 <= cols:
        lines = []
        for y in range(0, n, 2):
            top = matrix[y]
            bottom = matrix[y + 1] if y + 1 < n else [False] * n
            lines.append("".join(
                "█" if t and b else "▀" if t else "▄" if b else " "
                for t, b in zip(top, bottom)))
        return "\n".join(lines), "half"

    return None, "终端只有 %d 列，二维码至少需要 %d 列" % (cols, n + 2)


def write_qr_file(text, path):
    """把二维码写成文件，便于 scp 回本地用图形界面打开。

    支持三种后缀：
      .svg  矢量图，不需要额外依赖（推荐）
      .png  位图，需要 Pillow
      .txt  只保存原始内容，便于自己用别的工具生成
    """
    suffix = str(path).lower().rsplit(".", 1)[-1]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if suffix == "txt":
        target.write_text(text, encoding="utf-8")
        return target

    try:
        import qrcode
    except ImportError:
        raise LoginError("导出二维码需要 qrcode 库：python3 -m pip install qrcode")

    # 导出成图片时用规范要求的静默区宽度 4，扫码更稳
    qr = qrcode.QRCode(border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)

    if suffix == "svg":
        import qrcode.image.svg
        qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(str(target))
    elif suffix == "png":
        try:
            qr.make_image().save(str(target))
        except ImportError:
            raise LoginError("导出 PNG 需要 Pillow：python3 -m pip install pillow\n"
                             "或者改用 .svg（不需要额外依赖）")
    else:
        raise LoginError("不支持的格式 .%s，请用 .svg / .png / .txt" % suffix)
    return target


# --------------------------------------------------------------------------- #
# 扫码登录
# --------------------------------------------------------------------------- #
def _open(opener, url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Referer": "https://www.bilibili.com/",
        "Accept-Encoding": "identity",
    })
    try:
        with opener.open(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001
        raise LoginError("请求失败：%r" % (exc,))


def qrcode_login(timeout=180, poll_interval=2.0, on_qr=None, on_status=None):
    """扫码登录，成功后返回 (cookie_string, cookies_dict)。

    on_qr(url)      —— 拿到二维码内容时回调，用于展示
    on_status(code) —— 每次轮询回调，用于展示进度
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    status, data = _open(opener, GENERATE_URL)
    payload = data.get("data") or {}
    key, url = payload.get("qrcode_key"), payload.get("url")
    if data.get("code") != 0 or not key:
        raise LoginError("无法获取二维码：%s" % (data.get("message") or status))

    if on_qr:
        on_qr(url)

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval + random.uniform(0, 0.4))
        _, d = _open(opener, "%s?qrcode_key=%s" % (POLL_URL, urllib.parse.quote(key)))
        p = d.get("data") or {}
        pc = p.get("code")
        if on_status:
            on_status(pc)

        if pc == 0:
            cookies = {c.name: c.value for c in jar if "bilibili" in (c.domain or "")}
            # 有些情况下 Cookie 是放在返回的 url 查询串里的，一并兜住
            for k, v in urllib.parse.parse_qsl(urllib.parse.urlparse(p.get("url") or "").query):
                cookies.setdefault(k, v)
            if not cookies.get("SESSDATA"):
                raise LoginError("登录返回成功，但没有拿到 SESSDATA")
            cookie_str = "; ".join("%s=%s" % (k, v) for k, v in cookies.items())
            return cookie_str, cookies

        if pc == 86038:
            raise LoginError("二维码已过期（180 秒），请重新运行 login")

    raise LoginError("等待扫码超时。")


def check_login(client):
    """检查当前登录态是否有效。

    返回 dict：{ok, mid, name, code, message}
    未登录/过期时 B站 nav 接口返回 code=-101。
    """
    try:
        _, data = client._get("/x/web-interface/nav", referer="https://www.bilibili.com/")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": None, "message": "请求失败：%r" % (exc,)}
    code = data.get("code")
    d = data.get("data") or {}
    if code == 0 and d.get("isLogin"):
        return {"ok": True, "code": code, "mid": d.get("mid"),
                "name": d.get("uname"), "avatar": d.get("face"), "message": "已登录"}
    # 只要 nav 拿回了 wbi_img，就说明凭据还能用（只是没登录）
    msg = "未登录" if code == -101 or (code == 0 and d.get("isLogin") is False) else (data.get("message") or "未知状态")
    return {"ok": False, "code": code, "message": msg}
