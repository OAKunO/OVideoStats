#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""浏览器自动化的公共部分。

抖音和视频号走的是同一条路——**持久化浏览器用户目录 + 拦截页面自己发的请求**，
因为这两家的网页接口都带签名（抖音是 a_bogus），纯 HTTP 调不通；而登录页又都
有反爬 SDK，纯 Python 无头登录也不行。用真实浏览器一切都自然满足。

两个平台只有「打开哪个页面、拦截哪个接口、怎么解析」不同，其余完全一样，
所以抽到这里，避免同一套逻辑写两遍。

登录态存在浏览器用户目录里（不是 Cookie 文件），因此换机器只要拷目录即可，
也完全绕开了签名问题。

⚠️ 通用踩坑记录（两个平台都适用）：
  1. **UA 里不能带 `HeadlessChrome`**。抖音会在导出请求里读 UA 的版本号，
     认出无头就拒绝下发文件（表现：下载永远停在 0 字节的 .crdownload）。
  2. **Playwright 的下载机制不可靠**。在 `page.on("download")` 回调里调
     `save_as()` 会直接弄死页面（同步 API 不允许在事件回调里调同步方法）。
  3. **headless 下登录页往往渲染不出来**，务实做法是开有头窗口让用户扫。
"""

import time
import sys
from pathlib import Path
from .browser_selection import BrowserSelectionError, bind_profile, select_browser

# 无头 Chrome 默认 UA 带 HeadlessChrome，很多平台会据此拒绝服务。
UA_PLATFORM = "Windows NT 10.0; Win64; x64" if sys.platform == "win32" else "Macintosh; Intel Mac OS X 10_15_7"
DESKTOP_UA = ("Mozilla/5.0 (" + UA_PLATFORM + ") AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

# 判断「未登录」的通用线索
LOGGED_OUT_HINTS = ("/login", "login.html", "passport", "扫码登录", "登录视频号")


class BrowserError(Exception):
    """浏览器流程失败。"""


class NeedLogin(BrowserError):
    """需要人工扫码登录。"""


# Chrome 在用户目录里放这几个锁文件。上一次运行没干净退出（崩溃、被 Ctrl-C、
# 被 kill）就会留下它们，下次启动时报
#   Failed to create a ProcessSingleton for your profile directory
# 然后直接拒绝启动。所以每次启动前先清掉。
PROFILE_LOCK_FILES = ("SingletonLock", "SingletonCookie", "SingletonSocket")


def clean_profile_locks(profile_dir):
    """清掉上次遗留的 Chrome 单例锁。返回清掉了几个。"""
    removed = 0
    for name in PROFILE_LOCK_FILES:
        path = Path(profile_dir) / name
        try:
            if path.is_symlink() or path.exists():
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrowserError(
            "需要 Playwright：\n"
            "    python3 -m pip install playwright\n"
            "驱动本机已安装的 Chrome 或 Edge，不需要再执行 playwright install。")
    return sync_playwright


class BrowserSession:
    """管理一个持久化浏览器上下文。用 with 语句使用。

        with BrowserSession(profile_dir=...) as s:
            s.open()
            s.goto(URL)
            if s.need_login():
                s.wait_for_login()
    """

    #: 子类覆盖：未登录时页面 URL/文案里会出现这些线索
    logged_out_hints = LOGGED_OUT_HINTS

    def __init__(self, profile_dir, headless=True, channel=None,
                 timeout=40000, slow_mo=0, user_agent=None):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.channel = channel
        self.timeout = timeout
        self.slow_mo = slow_mo
        self.user_agent = user_agent or DESKTOP_UA
        self._pw = None
        self._ctx = None
        self._native_chrome = None
        self.browser_choice = None
        self.browser_message = ''
        self.page = None

    # -- 生命周期 ---------------------------------------------------------- #
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def open(self):
        try:
            selection = select_browser(self.profile_dir, self.channel)
        except BrowserSelectionError as exc:
            raise BrowserError(str(exc)) from exc
        self.browser_choice = selection.browser
        self.browser_message = selection.message
        sync_playwright = require_playwright()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        bind_profile(self.profile_dir, self.browser_choice)
        clean_profile_locks(self.profile_dir)
        self._pw = sync_playwright().start()
        kwargs = {
            "headless": self.headless,
            # 不让 Chrome 接管下载：Playwright 下载机制在持久化无头上下文里
            # 不稳（见模块文档），需要文件的场景我们都自己用请求上下文取。
            "accept_downloads": False,
            "user_agent": self.user_agent,
            "viewport": {"width": 1440, "height": 900},
            "slow_mo": self.slow_mo,
        }
        kwargs['executable_path'] = str(self.browser_choice.executable)
        try:
            if sys.platform == 'darwin':
                from .mac_chrome import MacChromeSession
                self._native_chrome = MacChromeSession(app_path=self.browser_choice.app_path,
                                                       name=self.browser_choice.name)
                self._ctx = self._native_chrome.open(self._pw, self.profile_dir, self.headless,
                                                       self.user_agent, self.timeout, self.slow_mo)
            else:
                self._ctx = self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir), **kwargs)
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            self.close()
            if "ProcessSingleton" in message or "SingletonLock" in message:
                raise BrowserError(
                    "浏览器用户目录被占用了：\n"
                    "    %s\n\n"
                    "可能是上一次运行没有正常退出，留下了浏览器进程。\n"
                    "请关闭本应用为该账号打开的浏览器窗口，再重试。\n\n"
                    "（锁文件我们已经自动清理，但进程还在的话清掉也没用。）"
                    % self.profile_dir)
            raise BrowserError("启动浏览器失败：%s" % message[:300])
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        if self._native_chrome:
            self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.set_default_timeout(self.timeout)
        if not self.headless:
            self.bring_to_front()
        return self

    def bring_to_front(self):
        """把浏览器窗口提到最前。

        Playwright 启动的 Chrome 有时会开在别的窗口后面（macOS 上尤其明显），
        用户会以为「命令没反应」。所以有头模式下主动抬一下。
        """
        try:
            self.page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        return self

    def close(self):
        for step in (lambda: self._native_chrome.close() if self._native_chrome else self._ctx and self._ctx.close(),
                     lambda: self._pw and self._pw.stop()):
            try:
                step()
            except Exception:  # noqa: BLE001
                pass
        self._ctx = self._pw = None
        self._native_chrome = None

    # -- 页面 -------------------------------------------------------------- #
    @property
    def url(self):
        try:
            return self.page.url
        except Exception:  # noqa: BLE001
            return ""

    def goto(self, url, settle=5.0):
        self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
        time.sleep(settle)
        return self.page

    def body_text(self, limit=2000):
        try:
            return self.page.inner_text("body")[:limit]
        except Exception:  # noqa: BLE001
            return ""

    def count(self, selector):
        try:
            return self.page.locator(selector).count()
        except Exception:  # noqa: BLE001
            return 0

    def click_any(self, selector_groups):
        """按顺序尝试多组选择器，点中第一个可见的。返回是否点到。"""
        for group in selector_groups:
            for selector in (group if isinstance(group, (list, tuple)) else [group]):
                try:
                    loc = self.page.locator(selector).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=8000)
                        return True
                except Exception:  # noqa: BLE001
                    continue
        return False

    # -- 登录 -------------------------------------------------------------- #
    def is_logged_in(self):
        """默认判定：URL 没落到登录页，且正文里没有登录字样。

        子类可以覆盖成更精确的判断。
        """
        url = self.url.lower()
        if any(h in url for h in self.logged_out_hints if h.startswith("/")):
            return False
        text = self.body_text()
        return not any(h in text for h in self.logged_out_hints if not h.startswith("/"))

    def need_login(self):
        return not self.is_logged_in()

    def wait_for_login(self, timeout=180, poll=2.0, on_wait=None):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_logged_in():
                return True
            if on_wait:
                on_wait(int(deadline - time.time()))
            time.sleep(poll)
        raise NeedLogin("等待扫码超时（%d 秒）。重新运行即可再来一次。" % timeout)

    def grab_login_qr(self, save_to=None, min_size=100):
        """把页面上最大的 base64 PNG 当作登录二维码取出来。

        比截图干净：抖音的二维码就是一张 data:image/png;base64 的 img。
        视频号的二维码在 iframe 里，取不到时返回 None，让调用方改用有头窗口。
        """
        import base64
        import re
        srcs = self.page.evaluate("""(minSize) => {
          const out = [];
          const visit = (doc) => {
            try {
              doc.querySelectorAll('img, canvas').forEach(el => {
                const r = el.getBoundingClientRect();
                if (r.width >= minSize && Math.abs(r.width - r.height) < 20) {
                  if (el.tagName === 'IMG' && (el.src || '').startsWith('data:image'))
                    out.push({kind: 'img', data: el.src});
                  else if (el.tagName === 'CANVAS') {
                    try { out.push({kind: 'canvas', data: el.toDataURL('image/png')}); } catch (e) {}
                  }
                }
              });
              doc.querySelectorAll('iframe').forEach(f => {
                try { if (f.contentDocument) visit(f.contentDocument); } catch (e) {}
              });
            } catch (e) {}
          };
          visit(document);
          return out;
        }""", min_size)
        if not srcs:
            return None
        raw = base64.b64decode(re.sub(r"^data:image/\w+;base64,", "", srcs[0]["data"]))
        if save_to:
            target = Path(save_to)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        return raw

    def dump_debug(self, path, reason):
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(path), full_page=True)
            raise BrowserError("%s（已截图 %s）" % (reason, path))
        except BrowserError:
            raise
        except Exception:  # noqa: BLE001
            raise BrowserError(reason)
