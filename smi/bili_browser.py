#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站浏览器登录 —— 给扫码加一个「开窗口」的选项。

B站 本来就有扫码登录（`smi login`，二维码直接渲染在终端里，连浏览器都不用开），
而且拿到 SESSDATA 后走的是纯 HTTP 接口，比浏览器采集快得多。
所以这个模块**不是要取代它**，而是补一个更一致的选项：

  * 终端二维码在某些终端/字体下不好扫（前面专门修过换行毁码的问题）
  * 抖音/视频号/小红书都是「开窗口扫」，B站 保持一致更省心

另外一个潜在好处：真实浏览器会话里带 **`bili_ticket` / `buvid_fp`** 等
完整指纹 Cookie，而当初用纯 HTTP 请求 `space/wbi/arc/search` 时是 `-412`。
拿这些 Cookie 去试，有可能解锁 100% 覆盖的官方列表接口。
"""

from pathlib import Path

from .browser_base import BrowserSession

PROFILE_DIR = Path("data/browser/bili")
HOME_URL = "https://www.bilibili.com/"
# B站的 Cookie 都在这些域下
COOKIE_DOMAINS = ("bilibili.com",)

# 这些不参与拼 Cookie 头（体积大且与接口无关）
SKIP_COOKIES = {"bmg_af_sc", "bmg_af_switch", "bmg_src_def_domain",
                "browser_resolution", "home_feed_column", "CURRENT_FNVAL",
                "CURRENT_QUALITY", "bp_video_offset_0"}


class BiliBrowser(BrowserSession):
    """B站。登录态存 data/browser/bili/。"""

    def __init__(self, profile_dir=None, headless=True, **kwargs):
        super().__init__(profile_dir=profile_dir or PROFILE_DIR,
                         headless=headless, **kwargs)

    def goto(self, url=HOME_URL, settle=6.0):
        return super().goto(url, settle=settle)

    def is_logged_in(self):
        """有头像就说明登录了。未登录时页面上有「登录」「立即登录」。

        注意不能用「正文里有『登录』」判断——B站首页导航本来就带这类文案，
        登录成功后也可能残留，所以先看头像这个正向特征。
        """
        if self.count(".header-avatar-wrap") or self.count(".bili-avatar"):
            return True
        return False

    def open_login_dialog(self):
        """点开登录弹窗（二维码在里面）。返回是否成功点开。"""
        for selector in ('text=登录', 'text=立即登录', '.header-login-entry'):
            try:
                loc = self.page.locator(selector).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=6000)
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def grab_login_qr(self, save_to=None, min_size=100):
        """B站的二维码是登录弹窗里的一张 base64 图片，基类能直接取。

        弹窗可能还没打开，所以取不到时先点一下「登录」再试。
        """
        raw = super().grab_login_qr(save_to=save_to, min_size=min_size)
        if raw is None:
            self.open_login_dialog()
            import time as _t
            _t.sleep(3)
            raw = super().grab_login_qr(save_to=save_to, min_size=min_size)
        return raw

    def cookie_string(self):
        """把浏览器会话里的 B站 Cookie 拼成请求头用的字符串。"""
        parts = []
        for cookie in self._ctx.cookies():
            domain = cookie.get("domain", "")
            if not any(d in domain for d in COOKIE_DOMAINS):
                continue
            if cookie["name"] in SKIP_COOKIES:
                continue
            parts.append("%s=%s" % (cookie["name"], cookie["value"]))
        return "; ".join(parts)

    def cookie_names(self):
        return sorted(c["name"] for c in self._ctx.cookies()
                      if any(d in c.get("domain", "") for d in COOKIE_DOMAINS))

    def wait_for_login(self, timeout=180, poll=2.0, on_wait=None):
        """等扫码完成。登录成功后 B站 弹窗会自己关掉、头像出现。"""
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_logged_in():
                return True
            if on_wait:
                on_wait(int(deadline - time.time()))
            time.sleep(poll)
        from .browser_base import NeedLogin
        raise NeedLogin("等待扫码超时（%d 秒）。重新运行即可再来一次。" % timeout)
