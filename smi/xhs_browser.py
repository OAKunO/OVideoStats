#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书创作服务平台数据采集 —— 浏览器路线。

和抖音/视频号同构：持久化浏览器用户目录 + 拦截页面自己发的请求。
小红书创作者后台的接口带 `x-s` / `x-t` 签名，纯 HTTP 调不通；用真实浏览器
则一切都自然满足。

接口路径与字段核对自开源实现
[creator-platform-data](https://github.com/orangexie05/creator-platform-data)：
  /statistics/data-analysis                     数据看板
  /statistics/note-detail?noteId=<id>           单篇笔记详情
  /api/galaxy/creator/datacenter/note/analyze/list   笔记列表（含曝光/观看等）

⚠️ 登录方式是**短信验证码**（不是扫码），所以必须开有头窗口手动登录一次。
"""

import time
from pathlib import Path

from .browser_base import BrowserSession
from .parse import as_text, find_list, metric, pick, to_ts

BASE = "https://creator.xiaohongshu.com"
LOGIN_URL = BASE + "/login"
DATA_URL = BASE + "/statistics/data-analysis"
NOTE_DETAIL_URL = BASE + "/statistics/note-detail"
# 「笔记管理」页——**不需要开通数据权限**，是主要的列表来源。
NOTE_MANAGE_URL = BASE + "/new/note-manager"
# 实测可用（v2），返回 data.notes
NOTE_LIST_MARK = "creator/note/user/posted"
# 数据看板的接口，需要账号开通「数据权限」，有的话能多拿到曝光/封面点击率
ANALYZE_LIST_MARK = "datacenter/note/analyze/list"

PROFILE_DIR = Path("data/browser/xhs")
DOWNLOAD_DIR = Path("data/downloads/xhs")

# 小红书的登录表单是**嵌在页面里**的，登录成功后也不跳转 URL（SPA 原地换内容）。
# 所以判定只能看内容，不能看 URL——早期版本用 "login" in url 判断，
# 结果在 /login 页面上登录成功后永远判定为未登录，白等 5 分钟。
LOGGED_OUT_MARKERS = ("短信登录", "发送验证码", "登录即同意", "扫码登录")
# 登录后才会出现的导航项（登录页的营销文案里没有这几个词）
LOGGED_IN_MARKERS = ("发布笔记", "笔记管理", "数据看板", "创作者中心")

# 统计字段名没有公开文档，按候选键逐个取第一个有值的。
# 名字来自 creator-platform-data 的字段表：曝光/观看/封面点击率/点赞/评论/
# 收藏/涨粉/分享/人均观看时长/弹幕，详情页另有 2秒退出率、完播率。
METRIC_KEYS = {
    "exposure": ("exposure", "impCount", "impression", "impressionCount",
                 "exposureCount", "readCount"),
    "views": ("view_count", "viewCount", "view", "views", "readCount", "read"),
    "cover_ctr": ("coverClickRate", "coverCtr", "ctr", "clickRate"),
    "like": ("likes", "like", "likeCount", "like_count"),
    "comment": ("comments_count", "comment", "comments", "commentCount"),
    "collect": ("collected_count", "collect", "favorites", "favCount", "collectCount"),
    "follow": ("follow", "increaseFans", "newFans", "followCount", "fansIncrease"),
    "share": ("shared_count", "share", "shares", "shareCount"),
    "avg_watch_time": ("avgWatchTime", "avgViewTime", "averageWatchTime",
                       "avgWatchSeconds", "perWatchTime"),
    "danmaku": ("danmaku", "danmakuCount"),
    "finish_rate": ("completionRate", "finishRate", "completeRate"),
    "bounce_2s": ("twoSecondExitRate", "twoSecExitRate", "exitRate2s"),
}


class XhsBrowser(BrowserSession):
    """小红书创作服务平台。登录态存 data/browser/xhs/。"""

    def __init__(self, profile_dir=None, headless=True, **kwargs):
        super().__init__(profile_dir=profile_dir or PROFILE_DIR,
                         headless=headless, **kwargs)

    def goto(self, url=DATA_URL, settle=6.0):
        return super().goto(url, settle=settle)

    def is_logged_in(self):
        """只看页面内容，**不看 URL**。

        小红书不跳转登录页：未登录时把登录表单嵌进当前页；登录成功后 SPA
        原地换内容，URL 可能还停在 /login。早期版本用 `"login" in url` 判断，
        结果在 /login 页面登录成功后永远判定为未登录，白等五分钟。

        规则：出现登录表单特征 → 未登录；出现后台导航 → 已登录；
        都没匹配到 → 保守判为未登录（宁可让用户重试，也别误判已登录、
        等到采集时才失败）。
        """
        text = self.body_text()
        if any(marker in text for marker in LOGGED_OUT_MARKERS):
            return False
        return any(marker in text for marker in LOGGED_IN_MARKERS)
def duration_seconds(item):
    """时长藏在 video_info.duration 里；别把整个 video_info 当值塞进表格。"""
    info = item.get("video_info")
    if isinstance(info, dict):
        for key in ("duration", "videoDuration", "length"):
            if isinstance(info.get(key), (int, float)):
                return int(info[key])
    for key in ("duration", "length"):
        if isinstance(item.get(key), (int, float)):
            return int(item[key])
    return None


def normalize_note(item):
    """归一化成与 B站/抖音/视频号 同形的结构。"""
    created = pick(item, "time", "publishTime", "publish_time", "createTime",
                    "create_time", "postTime")
    permission = str(pick(item, 'permission_msg', 'permissionMsg', default='') or '')
    code = pick(item, 'permission_code', 'permissionCode')
    if '仅自己可见' in permission or '私密' in permission or str(code) == '1':
        visibility = 'private'
    elif str(code) == '0' or '公开' in permission:
        visibility = 'public'
    else:
        visibility = 'unknown'
    return {
        "bvid": str(pick(item, "id", "noteId", "note_id", "noteIdStr",
                          "contentId", default="")),
        "item_id": pick(item, "id", "noteId", "note_id", "noteIdStr", "contentId"),
        "title": as_text(pick(item, "display_title", "displayTitle", "title",
                                "name", "desc")),
        "created": to_ts(created),
        "play": metric(item, METRIC_KEYS["views"]),
        "exposure": metric(item, METRIC_KEYS["exposure"]),
        "cover_ctr": metric(item, METRIC_KEYS["cover_ctr"]),
        "like": metric(item, METRIC_KEYS["like"]),
        "comment": metric(item, METRIC_KEYS["comment"]),
        "collect": metric(item, METRIC_KEYS["collect"]),
        "follow": metric(item, METRIC_KEYS["follow"]),
        "share": metric(item, METRIC_KEYS["share"]),
        "avg_watch_time": metric(item, METRIC_KEYS["avg_watch_time"]),
        "danmaku": metric(item, METRIC_KEYS["danmaku"]),
        "finish_rate": metric(item, METRIC_KEYS["finish_rate"]),
        "bounce_2s": metric(item, METRIC_KEYS["bounce_2s"]),
        "genre": pick(item, "type"),
        "length": duration_seconds(item),
        "url": pick(item, "url", "noteUrl", "shareUrl"),
        "visibility": visibility,
    }


class XhsCollector:
    """捕获列表接口的请求模板（含签名请求头），然后自己翻页取全量。

    两个坑：

    1. **必须带上捕获到的请求头**。小红书的签名在 `x-s` / `x-t` / `x-s-common`
       请求头里，只重放 URL 会被服务端拒掉——返回 `{code:0, success:true}`
       但 data 里什么都没有，看着像「成功但没数据」，很容易误判成账号没内容。
    2. 响应体不能事后读。同步 API 不允许在 `page.on("response")` 回调里调同步
       方法，离开回调后 Chrome 又回收了响应体。所以只能捕获请求再重放。

    列表来源用「笔记管理」页的接口，因为**它不需要账号开通数据权限**；
    数据看板的接口能多给曝光/封面点击率，但要求账号有数据权限，所以只作为可选。
    """

    def __init__(self, browser):
        self.browser = browser

    def collect(self, max_pages=20, on_progress=None):
        template = self._capture_template(NOTE_MANAGE_URL, NOTE_LIST_MARK)
        if not template:
            return [], ["没抓到笔记列表接口请求——可能页面没加载完，或接口改了"]
        return self._fetch_all_pages(template, max_pages, on_progress)

    def _capture_template(self, page_url, mark):
        captured = []

        def on_request(request):
            if mark in request.url:
                captured.append(request)

        self.browser.page.on("request", on_request)
        try:
            self.browser.goto(page_url, settle=0)
            deadline = time.time() + 15
            while time.time() < deadline and not captured:
                self.browser.page.wait_for_timeout(250)
            if not captured:
                # SPA 偶尔从缓存恢复、不发请求，重载兜底
                self.browser.page.reload(wait_until="domcontentloaded")
                deadline = time.time() + 15
                while time.time() < deadline and not captured:
                    self.browser.page.wait_for_timeout(250)
        finally:
            try:
                self.browser.page.remove_listener("request", on_request)
            except Exception:  # noqa: BLE001
                pass
        return captured[0] if captured else None

    def _fetch_all_pages(self, template, max_pages, on_progress):
        import urllib.parse

        url = template.url
        # 关键：签名在请求头里，必须一起带上
        headers = {k: v for k, v in dict(template.headers).items()
                   if k.lower() not in ("host", "content-length")}
        params = dict(urllib.parse.parse_qsl(
            urllib.parse.urlparse(url).query, keep_blank_values=True))

        notes, errors, seen = [], [], set()
        page = 0
        while page < max_pages:
            params["page"] = page
            target = url.split("?")[0] + "?" + urllib.parse.urlencode(params)
            try:
                response = self.browser._ctx.request.get(target, headers=headers)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                errors.append("第%d页请求失败：%s" % (page, str(exc)[:60]))
                break

            items = find_list(payload)
            if not items:
                if page == 0:
                    errors.append("第一页就没有笔记数据（接口结构可能变了）")
                break

            new = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                note = normalize_note(item)
                if not note["item_id"] or note["bvid"] in seen:
                    continue
                seen.add(note["bvid"])
                notes.append(note)
                new += 1

            if on_progress:
                on_progress(page + 1, len(notes))
            if new == 0:
                break
            page += 1
            time.sleep(1.0)
        return notes, errors
