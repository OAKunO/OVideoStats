#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号（微信视频号）数据采集 —— 浏览器路线。

做法和抖音完全一样，理由也一样：视频号创作者助手（channels.weixin.qq.com）
的数据都打在 `cgi-bin/mmfinderassistant-bin/*` 这组 **POST 接口**上，依赖
`_finder_auth` 登录态；登录页又加载微信开放平台的 `wxLogin.js`。
所以用持久化浏览器打开助手页，**拦截它自己发出的响应**即可拿到本账号数据，
不需要自己去签名。

接口路径来自开源实现 creatorhub 的 channels_fetcher.py（已核对）：
  /platform                          → auth/get_auth_info（账号资料）
  /platform/post/list                → post/post_list（视频作品 + 统计）
  /platform/post/finderNewLifePostList → post/post_list（图文作品）
  statistic/post_list                → 统计数据（与作品按 id 合并）

⚠️ 视频号**只有本账号数据，没有对外公开的作品接口**，所以它只能采自己，
   不能像 B站 那样监控对标账号。
"""

import time
from pathlib import Path
from urllib.parse import urlsplit

from .browser_base import BrowserSession
from .parse import as_text, find_list, metric, pick, to_ts, unwrap

BASE = "https://channels.weixin.qq.com"
PLATFORM_URL = BASE + "/platform"
LOGIN_URL = BASE + "/login.html"
# 视频和图文是两套独立列表，都请求 post/post_list，只是 userpageType 不同
# （Video=11 / Photo=10）。只打开其中一个会漏掉另一半作品。
POST_LIST_URL = BASE + "/platform/post/list"
IMAGE_POST_LIST_URL = BASE + "/platform/post/finderNewLifePostList"
LIST_URLS = (POST_LIST_URL, IMAGE_POST_LIST_URL)

POST_API_MARK = "mmfinderassistant-bin/post/post_list"
STAT_API_MARK = "mmfinderassistant-bin/statistic/post_list"
AUTH_API_MARK = "mmfinderassistant-bin/auth/"

PROFILE_DIR = Path("data/browser/channels")
DOWNLOAD_DIR = Path("data/downloads/channels")

# 视频号的统计字段名没有公开文档，这里把常见的几种写法都列上，按顺序取第一个有值的
METRIC_KEYS = {
    "play": ("playCount", "play_count", "readCount", "read_count",
             "viewCount", "view_count", "exposureCount", "exposure_count",
             "readCnt", "playCnt", "viewCnt", "exposureCnt"),
    "like": ("likeCount", "like_count", "thumbCount", "thumb_count"),
    "comment": ("commentCount", "comment_count"),
    "share": ("forwardCount", "forward_count", "shareCount", "share_count"),
    "collect": ("favCount", "fav_count", "collectCount", "collect_count"),
    "follow": ("followCount", "follow_count", "subscribeCount"),
    "recommend": ("recommendCount", "recommend_count"),
    "bounce_2s": ("fastFlipRate", "fast_flip_rate"),
    "finish_rate": ("finishRate", "finish_rate", "fullPlayRate"),
    "avg_play_time": ("avgPlayTimeSec", "avgPlayTime", "avg_play_time",
                      "averagePlayTime"),
}
# 不同接口可能把统计塞在不同层级，逐个兜底
STAT_CONTAINERS = ("statistics", "stat", "stats", "postStat", "data", None)


class ChannelsBrowser(BrowserSession):
    """视频号助手。登录态存 data/browser/channels/。"""

    def __init__(self, profile_dir=None, headless=True, **kwargs):
        super().__init__(profile_dir=profile_dir or PROFILE_DIR,
                         headless=headless, **kwargs)

    # -- 登录 -------------------------------------------------------------- #
    def body_text(self, limit=2000):
        # Wujie can expose multiple body elements through its open shadow root.
        # A strict inner_text('body') query fails on an authenticated manager.
        try:
            return '\n'.join(self.page.locator('body').all_inner_texts())[:limit]
        except Exception:
            return ''

    def open_login(self):
        """Open the official QR entry once; confirmation remains with the user."""
        self.goto(LOGIN_URL, settle=2)
        try:
            switch = self.page.get_by_text('使用其他头像、昵称或账号', exact=True)
            if switch.count() and switch.first.is_visible():
                switch.first.click(timeout=5000)
        except Exception:
            pass  # The same choice remains available for the user in the page.

    def is_logged_in(self):
        """落到 login.html、或者页面上还有登录入口，就是没登录。

        不能用「正文里出现『登录』」来判断——助手首页的导航和营销文案里
        本来就有「登录视频号助手」「内容管理」「数据中心」这些词，会误判。
        参考实现（creatorhub）用的是 URL + **精确匹配**的「登录」按钮。
        此外营销首页有「一站式服务」和「登录视频号助手」组合，
        之前在二维码加载失败时会被误判为已登录。
        """
        url = urlsplit(self.url)
        path = url.path.lower()
        if url.scheme != 'https' or url.hostname != 'channels.weixin.qq.com':
            return False
        if not (path == '/platform' or path.startswith('/platform/')):
            return False
        if any(marker in path for marker in ('login', 'passport', 'oauth-callback')):
            return False
        if self.count('text="登录"') > 0:
            return False
        if self.count("text=微信扫码登录") > 0:
            return False
        text = self.body_text(3000)
        if not text.strip():
            return False  # Page is still loading; absence of a login prompt proves nothing.
        if "一站式服务" in text and "登录视频号助手" in text:
            return False
        # The current signed-in dashboard can show only the account summary;
        # the old "内容管理 / 数据中心" navigation is no longer always present.
        if "视频号ID:" in text and "昨日数据" in text:
            return True
        if self.count('.post-feed-item') and self.count('text="视频管理"'):
            return True
        return "内容管理" in text and "数据中心" in text

    def wait_for_login(self, timeout=180, poll=2.0, on_wait=None):
        """Observe authorization without refreshing a QR awaiting confirmation.

        Expired sessions can retain finder_username/sessionid/wxuin. Those
        markers cannot justify a redirect or prove that login has completed.
        """
        from .browser_base import NeedLogin
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            original = self.page
            pages = list(self._ctx.pages) if self._ctx else [original]
            available = []
            for page in pages:
                try:
                    if page.is_closed():
                        continue
                    available.append(page)
                    if urlsplit(page.url).hostname != 'channels.weixin.qq.com':
                        continue
                    self.page = page
                    if self.is_logged_in():
                        return True
                except Exception:
                    continue  # OAuth tabs may close during phone confirmation.
            if not available:
                raise NeedLogin('扫码窗口已关闭，视频号登录尚未完成，请重新连接。')
            self.page = original if original in available else available[0]
            if on_wait:
                on_wait(max(0, int(deadline - time.monotonic())))
            self.page.wait_for_timeout(max(100, int(poll * 1000)))
        raise NeedLogin('等待视频号扫码登录超时，请重新连接并在手机上确认。')


def normalize_post(item):
    """归一化成与 B站/抖音 同形的结构，好复用导出与统计。"""
    created = pick(item, "createTime", "create_time", "postTime", "post_time",
                    "publishTime", "publish_time")
    visible_type = pick(item, 'visibleType', 'visible_type')
    visible_name = as_text(pick(item, 'visibleName', 'visible_name'))
    if str(visible_type) == '1' or '公开' in visible_name:
        visibility = 'public'
    elif str(visible_type) in ('2', '3') or any(word in visible_name for word in ('仅自己', '私密', '仅关注')):
        visibility = 'private'
    else:
        visibility = 'unknown'
    return {
        "bvid": str(pick(item, "objectId", "object_id", "exportId",
                          "export_id", "id", default="")),
        "item_id": pick(item, "objectId", "object_id", "exportId", "export_id", "id"),
        "title": as_text(pick(item, "title", "desc", "description",
                                "content", "postTitle")),
        "created": to_ts(created),
        "play": metric(item, METRIC_KEYS["play"]),
        "like": metric(item, METRIC_KEYS["like"]),
        "comment": metric(item, METRIC_KEYS["comment"]),
        "share": metric(item, METRIC_KEYS["share"]),
        "collect": metric(item, METRIC_KEYS["collect"]),
        "follow": metric(item, METRIC_KEYS["follow"]),
        "recommend": metric(item, METRIC_KEYS["recommend"]),
        "finish_rate": metric(item, METRIC_KEYS["finish_rate"]),
        "avg_play_time": metric(item, METRIC_KEYS["avg_play_time"]),
        "bounce_2s": metric(item, METRIC_KEYS["bounce_2s"]),
        "length": None,
        "url": pick(item, "url", "shareUrl", "share_url"),
        "visibility": visibility,
    }


class ChannelsCollector:
    """把浏览器会话 + 接口重放 + 解析串起来。

    为什么要「捕获请求再重放」，而不是直接拦截响应：

      Playwright 同步 API 不允许在 `page.on("response")` 回调里调同步方法
      （调了会直接弄死页面，抖音那边踩过）。而离开回调之后再读就晚了——
      Chrome 会回收响应体，报
        Protocol error (Network.getResponseBody): No resource with given identifier found

      所以改成：打开列表页拿到一次请求的「模板」（URL + POST body），
      然后用浏览器上下文的会话自己翻页请求。完全可控，也不受回收影响。
    """

    def __init__(self, browser):
        self.browser = browser

    def collect(self, max_pages=10, on_progress=None):
        """用页面发出的真实请求作模板，按作品类型读取视频和图文。"""
        merged = {}
        errors = []
        # 2026 年的助手可能只在 finderNewLifePostList 路由发请求，且该
        # 请求的 userpageType=11 实际是视频。不能按页面 URL 猜作品类型。
        template = self._capture_template(IMAGE_POST_LIST_URL)
        if not template:
            template = self._capture_template(POST_LIST_URL)
        if not template:
            template = self._capture_template(IMAGE_POST_LIST_URL)
        if not template:
            return [], ["作品列表：没抓到接口请求"]
        for kind, label in ((11, "视频"), (10, "图文")):
            works, err = self._fetch_all_pages(template, label, max_pages, on_progress,
                                               userpage_type=kind)
            errors.extend(err)
            for work in works:
                merged[work["bvid"]] = {**merged.get(work["bvid"], {}), **work}
        return list(merged.values()), errors

    def _capture_template(self, list_url):
        """打开列表页，抓一个 post_list 请求当模板。

        只存 Request 对象、不读响应——request.post_data 是客户端缓存的，
        事后再读也安全。
        """
        captured = []

        def on_request(request):
            if "post/post_list" in request.url:
                captured.append(request)

        self.browser.page.on("request", on_request)
        try:
            self.browser.goto(list_url, settle=0)
            # 创作者后台首次加载要拉取多组资料；实测视频列表接口可能
            # 在页面导航后 30 秒左右才发出。
            deadline = time.time() + 20
            while time.time() < deadline and not captured:
                self.browser.page.wait_for_timeout(250)
        finally:
            try:
                self.browser.page.remove_listener("request", on_request)
            except Exception:  # noqa: BLE001
                pass
        return captured[0] if captured else None

    def _fetch_all_pages(self, template, label, max_pages, on_progress,
                         userpage_type=None):
        """用捕获到的模板翻页取全部作品。

        翻页依据有两条，任一满足就继续：totalCount 还没取满、continueFlag 为真。
        """
        import json
        url = template.url
        try:
            body = json.loads(template.post_data or "{}")
        except Exception:  # noqa: BLE001
            body = {}
        if userpage_type is not None:
            body["userpageType"] = userpage_type

        works, errors = [], []
        seen = set()
        total = None
        page = 1
        while page <= max_pages:
            body["currentPage"] = page
            if "timestamp" in body:
                body["timestamp"] = str(int(time.time() * 1000))
            try:
                response = self.browser._ctx.request.post(
                    url, data=json.dumps(body),
                    headers={"Content-Type": "application/json"})
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                errors.append("%s第%d页请求失败：%s" % (label, page, str(exc)[:60]))
                break

            data = unwrap(payload)
            err_code = pick(payload, "errCode", "err_code", default=0)
            if err_code not in (0, "0", None, ""):
                errors.append("%s第%d页 errCode=%s" % (label, page, err_code))
                break

            total = pick(data, "totalCount", "total_count", default=total)
            items = find_list(payload)
            new_count = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                work = normalize_post(item)
                if not work["item_id"] or work["bvid"] in seen:
                    continue
                seen.add(work["bvid"])
                works.append(work)
                new_count += 1

            if on_progress:
                on_progress(label, page, len(works), total)

            has_more = pick(data, "continueFlag", "continue_flag",
                             "hasMore", "has_more")
            if not items or new_count == 0:
                break
            if total is not None and len(works) >= int(total):
                break
            if has_more in (False, 0, "0", None) and total is None:
                break
            if has_more in (False, 0, "0") and total is not None and len(works) >= int(total):
                break
            page += 1
            time.sleep(1.0)
        return works, errors
