#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音创作者中心采集 —— 走真实浏览器（Playwright）。

为什么最终是这条路（而不是纯 HTTP 调接口）：

  1. 抖音网页端的接口被 Argus 签名挡住（实测导出接口
     `/web/api/creator/item/list/download` 带 `a_bogus`），扫码登录页又是
     bdms + secsdk + 验证码 的完整反爬栈（probes/douyin.py 实测）；
  2. 查了一圈开源实现——MediaCrawler、data-scientist-community、
     xhs_douyin_content、TzFilm-Douyin-Tool——**没有一个直接调创作者中心的
     HTTP 接口**，全部用真实浏览器驱动页面。

所以本模块用持久化浏览器用户目录（登录态存在这里，不是 Cookie 文件，
因此完全绕开签名问题），登录后取官方的「导出数据」。

⚠️ 抖音特有的坑（通用坑见 browser_base）：
  * 导出请求的 `browser_version` 参数会被服务端读取，认出无头浏览器就
    **拒绝下发文件**（表现：下载永远停在 0 字节的 .crdownload）。
  * 抖音下载下来的文件**没有扩展名**。
  * 对「自见/私密」作品不计播放量，导出里是 0。
"""

import time
from pathlib import Path

from .browser_base import BrowserError, BrowserSession
from .parse import to_number, to_ts

PROFILE_DIR = Path("data/browser/douyin")
DOWNLOAD_DIR = Path("data/downloads/douyin")

# 创作者中心的作品管理页，官方「导出数据」按钮就在这里
CONTENT_MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"
# 导出接口的路径特征（实测：/web/api/creator/item/list/download，带 a_bogus 签名）
EXPORT_PATH_MARK = "/creator/item/list/download"

# 判断「已登录」的特征：出现作品管理界面
LOGGED_IN_MARKERS = [
    "text=投稿作品",
    "text=作品管理",
    "text=数据总览",
    "text=内容管理",
]

# 兼容旧引用名
DouyinBrowserError = BrowserError


class DouyinBrowser(BrowserSession):
    """抖音创作者中心。登录态存 data/browser/douyin/。"""

    def __init__(self, profile_dir=None, download_dir=None, headless=True,
                 channel=None, timeout=40000, slow_mo=0, user_agent=None):
        super().__init__(profile_dir=profile_dir or PROFILE_DIR, headless=headless,
                         channel=channel, timeout=timeout, slow_mo=slow_mo,
                         user_agent=user_agent)
        self.download_dir = Path(download_dir or DOWNLOAD_DIR)

    def goto(self, url=CONTENT_MANAGE_URL, settle=5.0):
        return super().goto(url, settle=settle)

    def is_logged_in(self):
        """出现作品管理界面才算登录。

        必须先看未登录特征：登录页上也有导航文字，只看正向特征会误判。
        """
        for marker in ("扫码登录", "验证码登录", "密码登录"):
            if marker in self.body_text():
                return False
        return any(self.count(selector) for selector in LOGGED_IN_MARKERS)

    def grab_login_qr(self, save_to=None, min_size=100):
        """抖音的登录二维码是页面上的一张 base64 PNG，基类已能直接取。"""
        raw = super().grab_login_qr(save_to=save_to, min_size=min_size)
        if raw is None:
            raise BrowserError("页面上找不到登录二维码，可能页面还没渲染完。")
        return raw

    def export_videos(self, timeout=120):
        """导出作品数据。

        这里绕了个弯，值得说明为什么：

        直接让 Chrome 下载是不稳定的——文件会先变成 0 字节的 .crdownload，
        而且浏览器上下文经常在这时自己挂掉（下载中间态 + 无头持久化上下文的
        组合有问题）。试过在 download 事件回调里 save_as（同步 API 不允许在
        回调里调同步方法，直接把页面弄死），也试过 expect_download（能接到
        事件，但 save_as 时报 context closed）。

        最终方案：**点击只是为了逼页面生成带 a_bogus 签名的请求 URL，
        拿到 URL 后用 Playwright 的请求上下文自己去取**。完全绕开 Chrome 的
        下载机制，稳定且可控。
        """
        self._dismiss_overlays()
        captured = []

        def on_request(request):
            if EXPORT_PATH_MARK in request.url:
                captured.append(request.url)

        self.page.on("request", on_request)
        try:
            if not self.click_any([('button:has-text("导出数据")',),
                                    ('text=导出数据',),
                                    ('button:has-text("导出")',)]):
                self._dump_debug("找不到导出按钮")
                raise DouyinBrowserError(
                    "找不到「导出数据」按钮。抖音的界面可能改版了，请把 "
                    "data/downloads/douyin/_debug.png 发我。")
            # 点完可能弹确认框，里头还有一层
            time.sleep(2)
            self.click_any([('button:has-text("开始导出")',),
                             ('button:has-text("确认导出")',),
                             ('button:has-text("确定")',)])

            deadline = time.time() + 30
            while time.time() < deadline and not captured:
                time.sleep(0.5)
        finally:
            try:
                self.page.remove_listener("request", on_request)
            except Exception:  # noqa: BLE001
                pass

        if not captured:
            self._dump_debug("没有发出导出请求")
            raise DouyinBrowserError(
                "点了「导出数据」但页面没有发出导出请求。可能是导出被限流，"
                "或界面改版了（已截图 _debug.png）。稍等一会儿再试。")

        url = captured[0]
        response = self._ctx.request.get(url, timeout=timeout * 1000)
        if response.status != 200:
            raise DouyinBrowserError("导出请求返回 HTTP %s" % response.status)
        body = response.body()
        if not body:
            raise DouyinBrowserError("导出请求返回了空内容（可能被限流了）")

        ext = ".xlsx" if body[:4] == b"PK\x03\x04" else ".csv"
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = self.download_dir / ("抖音作品数据_%s%s" % (stamp, ext))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return [target]

    def _dismiss_overlays(self):
        """关掉挡住点击的引导浮层（实测有「点击这里进入评论管理 / 知道了」）。"""
        for selector in ('button:has-text("知道了")', 'text=知道了',
                         '[class*=guide] [class*=close]', '[class*=tooltip] [class*=close]'):
            try:
                loc = self.page.locator(selector).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=3000)
                    time.sleep(0.8)
            except Exception:  # noqa: BLE001
                continue

    def _wait_downloads(self, before, timeout):
        """等新文件出现且大小稳定。

        两条路都看：downloads_path 落盘的文件，和 download 事件里显式
        保存的文件。Playwright 的行为在不同版本/平台上有差异，兜住两种。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            now = set(self.download_dir.glob("*")) - before
            now = {p for p in now
                   if not p.name.startswith((".", "_"))
                   and not p.name.endswith(".crdownload")}
            if now:
                sizes = {p: p.stat().st_size for p in now}
                time.sleep(1.5)
                if all(p.stat().st_size == s and s > 1000 for p, s in sizes.items()):
                    return sorted(now)
            time.sleep(1.0)
        self._dump_debug("等下载超时")
        raise DouyinBrowserError(
            "点了导出但没等到下载文件（%d 秒）。可能是抖音改了导出流程。" % timeout)

    def _dump_debug(self, reason):
        try:
            path = self.download_dir / "_debug.png"
            self.page.screenshot(path=str(path), full_page=True)
            raise DouyinBrowserError("%s（已截图 %s）" % (reason, path))
        except DouyinBrowserError:
            raise
        except Exception:  # noqa: BLE001
            raise DouyinBrowserError(reason)


# --------------------------------------------------------------------------- #
# 解析抖音导出的表格
# --------------------------------------------------------------------------- #
# 抖音导出的表用中文列名，不同入口（列表导出/单作品详情导出）列的多少不一样。
# 这里做模糊匹配，只挂我们认识的列，其余原样保留。
COLUMN_MAP = {
    "作品名称": "title", "视频名称": "title", "标题": "title", "作品标题": "title",
    "发布时间": "created", "发布日期": "created", "创建时间": "created",
    "播放量": "play", "观看量": "play", "播放次数": "play",
    "点赞量": "like", "点赞数": "like",
    "评论量": "comment", "评论数": "comment",
    "分享量": "share", "分享数": "share", "转发量": "share",
    "收藏量": "collect", "收藏数": "collect",
    "作品id": "item_id", "作品ID": "item_id", "视频id": "item_id", "视频ID": "item_id",
    "完播率": "finish_rate", "5s完播率": "finish_rate_5s",
    "封面点击率": "cover_ctr", "2s跳出率": "bounce_2s",
    "平均播放时长": "avg_play_time",
    "体裁": "genre", "审核状态": "status", "数据日期": "data_date",
    "主页访问量": "profile_visits", "粉丝增量": "fans_delta",
}

# 抖音对「自见 / 私密」的作品不计播放量，导出里播放量就是 0。
# 统计时必须把它们排除，否则播放量中位数会被拉成 0，毫无意义。
NON_PUBLIC_STATUS = ("自见", "私密", "仅自己可见", "审核中", "未通过", "已删除")


def is_public(video):
    """作品是否公开可见。抖音的审核状态字段决定播放量是否有意义。"""
    status = str(video.get("status") or "")
    return not any(word in status for word in NON_PUBLIC_STATUS)


def parse_export_file(path):
    """解析抖音导出的 xlsx / csv，返回归一化后的作品列表。

    导出表通常前面有一两行说明文字，真正的表头不一定在第一行，所以
    先扫描找到含「播放量」或「作品名称」的那一行当表头。
    """
    path = Path(path)
    # 抖音下载下来的文件是**没有扩展名**的（形如 64a32e89-...），
    # 所以按内容嗅探，而不是看后缀。
    with open(path, "rb") as fh:
        head = fh.read(8)
    is_zip = head[:4] == b"PK\x03\x04"
    looks_csv = b"\x00" not in head

    if is_zip or path.suffix.lower() in (".xlsx", ".xlsm"):
        import warnings
        from openpyxl import load_workbook
        warnings.filterwarnings("ignore", message="Workbook contains no default style")
        # 必须传文件对象：openpyxl 只看**扩展名**判断格式，而抖音下载下来的
        # 文件没有扩展名，直接传路径会抛 InvalidFileException。
        with open(path, "rb") as fh:
            wb = load_workbook(fh, read_only=True, data_only=True)
            ws = wb.active
            rows = [[c for c in row] for row in ws.iter_rows(values_only=True)]
            wb.close()
    elif looks_csv or path.suffix.lower() == ".csv":
        import csv
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            rows = [r for r in csv.reader(fh)]
    else:
        raise DouyinBrowserError(
            "认不出导出文件的格式（前 8 字节：%r）。多半是抖音改了导出格式。" % head)

    if not rows:
        raise DouyinBrowserError("导出文件是空的：%s" % path)

    header_idx, headers = None, []
    for i, row in enumerate(rows[:10]):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if any(c in COLUMN_MAP for c in cells):
            header_idx, headers = i, cells
            break
    if header_idx is None:
        raise DouyinBrowserError(
            "在导出文件的前 10 行里找不到认识的表头。实际表头：%s"
            % [str(c) for c in (rows[0] if rows else [])][:12])

    videos = []
    for row in rows[header_idx + 1:]:
        if not any(c not in (None, "") for c in row):
            continue
        raw = dict(zip(headers, row))
        item = {"_raw": raw}
        for col, value in raw.items():
            field = COLUMN_MAP.get(str(col).strip())
            if not field:
                continue
            if field == "created":
                item[field] = to_ts(value)
            elif field in ("title", "item_id", "genre", "status", "data_date"):
                item[field] = str(value).strip() if value is not None else ""
            else:
                item[field] = to_number(value)

        item.setdefault("title", "")
        item["bvid"] = str(item.get("item_id") or item.get("title", ""))
        item.setdefault("length", None)
        item.setdefault("url", None)
        videos.append(item)
    return videos
