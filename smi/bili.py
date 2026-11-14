#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站数据采集。

所有结论都经过 probes/ 实测，不是猜的：

  * ``/x/web-interface/view?bvid=``   未登录可读，播放量、点赞量等字段权威齐全。
  * ``/x/frontend/finger/spi``        领取游客身份 buvid3。匿名访问即得，
                                      **不是账号凭据**；作用是降低被风控拦的概率。
  * ``/x/web-interface/search/type``  带上游客身份即可用！以 UP 名为关键词按发布
                                      时间排序，可列出该 UP 作品，且 play/like/弹幕/
                                      收藏一次全返回。**这是「不登录」方案的主力。**
  * ``/x/web-interface/archive/related`` 相关推荐，可沿同 UP 视频做 BFS 扩散补充。
  * ``/x/space/wbi/arc/search``       未登录返回 -412，**必须带登录 Cookie**
                                      才能列出某个账号的作品。wbi 签名本身已验证正确。
  * ``/x/web-interface/newlist``      ⚠️ 陷阱：它忽略 mid 参数，返回与目标账号
                                      无关的内容，且 stat 全部被零值化成 0。不要用。
  * ``/x/space/arc/search``           旧接口，已下线（-799）。
  * 主页 HTML 的 __INITIAL_STATE__    已不再输出作品列表。

三条采集路径的取舍：

  1. 登录态      → ``space/wbi/arc/search``，覆盖 100%，但需要交出账号凭据。
  2. 不登录      → ``search/type``，实测覆盖约 95%，2 次请求搞定，零凭据。**默认走这条。**
  3. 不登录补充  → ``archive/related`` 的 BFS，补搜索漏掉的部分。
"""

import hashlib
import json
import os
import random
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TIMEOUT = 15
API = "https://api.bilibili.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
_SSL = ssl.create_default_context()

# 移动端 UA：空间页 HTML 在这套 UA 下会直出昵称
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
             "Mobile/15E148 Safari/604.1")

# B站前端公开的 mixinKey 重排表
_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

# mid → 昵称 的本地缓存。card 接口是概率性风控，缓存能避免每次都去赌它。
NAME_CACHE_FILE = Path("data/cache/account_names.json")


class BiliError(Exception):
    """B站接口返回了非预期结果。"""


class NeedLogin(BiliError):
    """接口要求登录态。"""


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def parse_mid(text):
    """从主页网址、带参数的网址或纯数字里取出 mid。

    >>> parse_mid("https://space.bilibili.com/12345/video?spm_id_from=333")
    12345
    """
    text = (text or "").strip()
    if text.isdigit():
        return int(text)
    m = re.search(r"space\.bilibili\.com/(\d+)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{3,})\b", text)
    return int(m.group(1)) if m else None


def parse_bvid(text):
    """从视频网址或裸 bvid 里取出 bvid。"""
    text = (text or "").strip()
    m = re.search(r"(BV[0-9A-Za-z]{10})", text)
    return m.group(1) if m else None


def normalize_cookie(raw):
    """允许填整条 Cookie 头，也允许只填 SESSDATA 的值。"""
    if not raw:
        return None
    raw = raw.strip()
    if "\n" in raw:
        raw = raw.split("\n", 1)[0].strip()
    if "=" not in raw:
        # 只给了 SESSDATA 的值
        return "SESSDATA=" + raw
    return raw


def load_name_cache(path=None):
    # 注意：这里刻意不用默认参数绑定，而是在调用时解析模块常量，
    # 否则测试无法把它指向临时文件，会写脏真实数据目录。
    path = path or NAME_CACHE_FILE
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_name_cache(cache, path=None):
    path = path or NAME_CACHE_FILE
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def load_cookie(explicit=None):
    """按优先级找登录态：显式参数 → 环境变量 → 扫码登录凭据。

    凭据由 `smi login` 写入 data/credentials/bilibili.json；也支持用
    BILI_COOKIE / BILI_SESSDATA 环境变量注入（容器 / systemd secret 场景）。
    """
    if explicit:
        return normalize_cookie(explicit)
    for env in ("BILI_COOKIE", "BILI_SESSDATA"):
        if os.environ.get(env):
            return normalize_cookie(os.environ[env])

    from .auth import load_credentials
    cred = load_credentials()
    return normalize_cookie(cred["cookie"]) if cred else None


_TAG_RE = re.compile(r"<[^>]+>")

# 风控类返回码：这些是「暂时被拦」，值得重试；其它则说明请求本身有问题
RISK_CODES = (-412, -352, -799, -509, -403)


def strip_tags(text):
    """搜索结果的标题里带 <em class="keyword"> 高亮标签，要去掉。"""
    if not text:
        return ""
    import html
    return html.unescape(_TAG_RE.sub("", str(text))).strip()


def to_int(value):
    """搜索接口的数值字段有时是字符串，统一成 int。"""
    if value is None or value == "--":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


# --------------------------------------------------------------------------- #
# wbi 签名
# --------------------------------------------------------------------------- #
def mixin_key(img_key, sub_key):
    raw = img_key + sub_key
    return "".join(raw[i] for i in _MIXIN_TAB)[:32]


def sign_params(params, mk):
    params = dict(params)
    params["wts"] = int(time.time())
    query = urllib.parse.urlencode(
        [(k, "".join(c for c in str(v) if c not in "!'()*"))
         for k, v in sorted(params.items())]
    )
    params["w_rid"] = hashlib.md5((query + mk).encode("utf-8")).hexdigest()
    return urllib.parse.urlencode(sorted(params.items()))


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class BiliClient:
    def __init__(self, cookie=None, delay=(1.0, 2.0), timeout=TIMEOUT):
        self.cookie = cookie          # 登录态，可选的加速/补全手段
        self.delay = delay
        self.timeout = timeout
        self._mk = None
        self._visitor = None          # 游客身份 buvid3，自动领取

    # -- 底层 ------------------------------------------------------------- #
    def _sleep(self):
        if self.delay and self.delay[1] > 0:
            time.sleep(random.uniform(*self.delay))

    def _cookie_header(self):
        parts = [p for p in (self._visitor, self.cookie) if p]
        return "; ".join(parts) if parts else None

    def ensure_visitor(self):
        """领取游客身份 buvid3/buvid4。

        这不是账号凭据：任何浏览器匿名访问 B站都会自动获得它。

        关于必要性：早期实测不带它时搜索接口返回 -412，但后续复验发现
        不带也能用（probes/verify.py 第 ⑤ 项会持续观察）。所以它更像
        「降低被拦概率」而不是硬性要求。成本只有一次请求，保留。
        """
        if self._visitor is not None:
            return self._visitor
        try:
            _, data = self._get("/x/frontend/finger/spi",
                                referer="https://www.bilibili.com/")
            d = data.get("data") or {}
            bits = []
            if d.get("b_3"):
                bits.append("buvid3=%s" % d["b_3"])
            if d.get("b_4"):
                bits.append("buvid4=%s" % d["b_4"])
            bits.append("b_nut=%d" % int(time.time()))
            self._visitor = "; ".join(bits)
        except BiliError:
            self._visitor = ""
        return self._visitor

    def _get_raw(self, url, referer=None, ua=None):
        """发请求，返回 (status, bytes)。用于非 JSON 的 HTML 页面。"""
        if not url.startswith("http"):
            url = API + url
        headers = {
            "User-Agent": ua or UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "identity",
        }
        if referer:
            headers["Referer"] = referer
        ck = self._cookie_header()
        if ck:
            headers["Cookie"] = ck

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=_SSL) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except Exception as exc:  # noqa: BLE001
            raise BiliError("请求失败：%r" % (exc,))

    def _get(self, url, referer=None):
        status, raw = self._get_raw(url, referer=referer)
        try:
            return status, json.loads(raw.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            raise BiliError("响应不是 JSON（HTTP %s）：%s" % (
                status, raw.decode("utf-8", "replace")[:120]))

    def _get_retry(self, url, referer=None, tries=3, base_delay=2.0):
        """对风控类错误做指数退避重试。

        B站的风控是概率性的：同一个请求可能这次 -412、下次就正常。
        因此单次失败绝不能直接判定整个采集失败。
        """
        last = None
        for attempt in range(tries):
            status, data = self._get(url, referer=referer)
            code = data.get("code")
            if code not in RISK_CODES:
                return status, data
            last = (status, data)
            if attempt < tries - 1:
                time.sleep(base_delay * (2 ** attempt) + random.uniform(0, 1.5))
        return last

    def _get_mixin(self):
        if self._mk:
            return self._mk
        _, data = self._get("/x/web-interface/nav", referer="https://www.bilibili.com/")
        wbi = (data.get("data") or {}).get("wbi_img") or {}
        img_key = re.sub(r"\.[^.]+$", "", (wbi.get("img_url") or "").rsplit("/", 1)[-1])
        sub_key = re.sub(r"\.[^.]+$", "", (wbi.get("sub_url") or "").rsplit("/", 1)[-1])
        if not (img_key and sub_key):
            raise BiliError("未能获取 wbi 密钥，B站接口可能已变更")
        self._mk = mixin_key(img_key, sub_key)
        return self._mk

    # -- 账号基础信息（无需登录态） ---------------------------------------- #
    def account_name(self, mid, tries=5):
        """账号昵称。这是搜索方案的关键（要拿它当关键词）。

        card 接口是**概率性**风控：同一个请求可能这次 -352、下次就正常。
        所以这里用比通用重试更耐心的退避（3/6/12/24 秒），并且成功一次就
        写进本地缓存，后续运行完全不再依赖这个接口。

        另有一条退路：置顶视频的作者名。但账号没有置顶视频时会返回 53016，
        这不是失败，只是没有这条路。
        """
        key = str(mid)
        cache = load_name_cache()
        if cache.get(key):
            return cache[key]        # 命中缓存 → 零网络请求

        self.ensure_visitor()
        ref = "https://space.bilibili.com/%s" % mid

        # 路径一：账号卡片，耐心重试
        for attempt in range(tries):
            try:
                _, data = self._get("/x/web-interface/card?mid=%s" % mid, referer=ref)
                code = data.get("code")
                name = ((data.get("data") or {}).get("card") or {}).get("name")
                if name:
                    cache[key] = name
                    save_name_cache(cache)
                    return name
                if code not in RISK_CODES:
                    break          # 不是风控问题，重试没意义
            except BiliError:
                break
            if attempt < tries - 1:
                time.sleep(3.0 * (2 ** attempt) + random.uniform(0, 1.5))

        # 路径二：移动端空间页 HTML（普通网页，限流强度低）
        name = self.account_name_from_page(mid)
        if name:
            cache[key] = name
            save_name_cache(cache)
            return name

        # 路径三：置顶视频的作者名（该账号可能压根没有置顶视频）
        try:
            _, data = self._get("/x/space/top/arc?vmid=%s" % mid, referer=ref)
            name = ((data.get("data") or {}).get("owner") or {}).get("name")
            if name:
                cache[key] = name
                save_name_cache(cache)
                return name
        except BiliError:
            pass

        return ""

    def account_name_from_page(self, mid):
        """从移动端空间页的 HTML 里抠昵称。

        这是最后一道退路。card 接口会被 B站**单独限流**（实测同 IP 下所有账号
        都 -352，而 navnum/view/search 全正常），但这个页面是普通网页，
        标题里就直出昵称：<title>oil欧呦的个人空间-...-哔哩哔哩视频</title>
        """
        try:
            _, raw = self._get_raw("https://m.bilibili.com/space/%s" % mid,
                                   referer="https://m.bilibili.com/", ua=MOBILE_UA)
        except BiliError:
            return ""
        html = raw.decode("utf-8", "replace")

        m = re.search(r"<title>([^<]{1,40}?)的个人空间", html)
        if m:
            return m.group(1).strip()
        m = re.search(r'"(?:uname|name)"\s*:\s*"([^"]{1,30})"', html)
        if m:
            return m.group(1).strip()
        return ""

    def account_video_count(self, mid):
        """官方作品计数，用作覆盖率参照。拿不到不影响主流程。"""
        self.ensure_visitor()
        try:
            _, data = self._get_retry(
                "/x/space/navnum?mid=%s" % mid,
                referer="https://space.bilibili.com/%s" % mid)
            return to_int((data.get("data") or {}).get("video"))
        except BiliError:
            return None

    # -- 不登录：用搜索接口列出账号作品 ------------------------------------ #
    # 翻页硬上限。关键词是通用词的账号（比如「搞机所」），搜索结果里会混进大量
    # 无关视频，numResults 可能上千；没有上限就会一路翻下去直到卡死。
    DEFAULT_MAX_PAGES = 50
    # 连续多少页没捞到新视频就算收敛
    EMPTY_STREAK_LIMIT = 3

    def search_account_videos(self, mid, name=None, max_pages=None,
                              expect=None, progress=None):
        """靠游客身份 + 搜索接口列出某 UP 的作品，**不需要登录态**。

        原理：search/type 带上 buvid3 游客 Cookie 即可用；以 UP 名为关键词、
        按发布时间排序，B站会把该 UP 的作品混排进结果。实测名字独特的账号
        可达 39/41（95%），且 play/like/弹幕/收藏一次全返回，无需逐条补齐。

        expect 传官方作品计数，用于提前收敛。
        返回 (videos, num_results)。
        """
        self.ensure_visitor()
        if not name:
            name = self.account_name(mid)
        if not name:
            raise BiliError("拿不到账号名，无法用搜索方式列举作品")

        cap = max_pages or self.DEFAULT_MAX_PAGES
        page = 1
        videos = []
        seen = set()
        num = None
        empty_streak = 0
        self.last_search_truncated = False

        while page <= cap:
            qs = urllib.parse.urlencode({
                "search_type": "video", "keyword": name,
                "order": "pubdate", "page": page,
            })
            _, data = self._get_retry(
                "/x/web-interface/search/type?" + qs,
                referer="https://search.bilibili.com/")
            code = data.get("code")
            if code in RISK_CODES:
                raise BiliError(
                    "搜索接口被风控拦截（code=%s）。稍等几分钟再试，"
                    "或降低请求频率。" % code)
            if code != 0:
                raise BiliError("搜索接口返回 code=%s %s" % (code, data.get("message")))

            payload = data.get("data") or {}
            num = to_int(payload.get("numResults")) or num
            results = payload.get("result") or []

            # 第一页之后按结果总数推算还需翻几页，避免无谓翻页
            if num and max_pages is None:
                cap = min(cap, max(1, -(-num // 20)))

            before = len(videos)
            for item in results:
                if str(item.get("mid")) != str(mid):
                    continue
                bvid = item.get("bvid")
                if not bvid or bvid in seen:
                    continue
                seen.add(bvid)
                videos.append({
                    "bvid": bvid,
                    "title": strip_tags(item.get("title")),
                    "created": to_int(item.get("pubdate")),
                    "length": item.get("duration"),
                    "play": to_int(item.get("play")),
                    "like": to_int(item.get("like")),
                    "comment": to_int(item.get("review")),
                    "danmaku": to_int(item.get("video_review")),
                    "favorite": to_int(item.get("favorites")),
                    "source": "search",
                })

            empty_streak = 0 if len(videos) > before else empty_streak + 1
            if progress:
                progress(page, len(videos), num)

            if expect and len(videos) >= expect:
                break
            if empty_streak >= self.EMPTY_STREAK_LIMIT:
                break
            if len(results) < 20:
                break
            if page >= cap:
                # 翻页上限截断：结果可能不全，必须让调用方知道
                self.last_search_truncated = True
                break
            page += 1
            self._sleep()

        return videos, num

    # -- 账号作品列表（需要登录态） ---------------------------------------- #
    def space_videos(self, mid, max_pages=None, progress=None):
        """返回 (videos, total)。videos 每项含 bvid/title/pubdate/play/comment。

        未登录时 B站会返回 -403/-412，这里翻译成 NeedLogin 并附上解决办法。
        """
        self.ensure_visitor()
        mk = self._get_mixin()
        page = 1
        videos = []
        total = None

        while True:
            qs = sign_params({
                "mid": mid, "ps": 30, "pn": page, "order": "pubdate",
                "platform": "web", "web_location": 1550101,
            }, mk)
            _, data = self._get_retry(
                "/x/space/wbi/arc/search?" + qs,
                referer="https://space.bilibili.com/%s/video" % mid,
            )
            code = data.get("code")
            if code in (-412, -403, -352):
                raise NeedLogin(
                    "B站拒绝了作品列表请求（code=%s %s）。该接口未登录必定被拦。"
                    % (code, data.get("message")))
            if code != 0:
                raise BiliError("作品列表接口返回 code=%s %s" % (code, data.get("message")))

            page_data = (data.get("data") or {}).get("page") or {}
            total = page_data.get("count", total)
            vlist = ((data.get("data") or {}).get("list") or {}).get("vlist") or []
            if not vlist:
                break

            for v in vlist:
                stat = v.get("stat") or {}
                videos.append({
                    "bvid": v.get("bvid"),
                    "title": (v.get("title") or "").strip(),
                    "created": v.get("created"),
                    "length": v.get("length"),
                    "play": stat.get("view"),
                    "comment": stat.get("comment"),
                    "like": None,  # 该接口不返回点赞量，需 view 补齐
                })

            if progress:
                progress(page, len(videos), total)

            if max_pages and page >= max_pages:
                break
            if total is not None and len(videos) >= total:
                break
            if len(vlist) < 30:
                break

            page += 1
            self._sleep()

        return videos, total

    # -- 单条视频详情（无需登录态） ---------------------------------------- #
    def video_detail(self, bvid):
        _, data = self._get_retry(
            "/x/web-interface/view?bvid=%s" % bvid,
            referer="https://www.bilibili.com/video/%s" % bvid,
        )
        if data.get("code") != 0:
            raise BiliError("视频 %s 详情接口返回 code=%s" % (bvid, data.get("code")))
        d = data.get("data") or {}
        stat = d.get("stat") or {}
        return {
            "bvid": d.get("bvid", bvid),
            "title": (d.get("title") or "").strip(),
            "created": d.get("pubdate"),
            "duration": d.get("duration"),
            "owner": (d.get("owner") or {}).get("name"),
            "mid": (d.get("owner") or {}).get("mid"),
            "view": stat.get("view"),
            "like": stat.get("like"),
            "reply": stat.get("reply"),
            "favorite": stat.get("favorite"),
            "coin": stat.get("coin"),
            "share": stat.get("share"),
            "danmaku": stat.get("danmaku"),
        }

    def fill_likes(self, videos, limit=None, progress=None):
        """逐条调用 view 补齐点赞量（列表接口不返回这个字段）。"""
        targets = videos if limit is None else videos[:limit]
        for i, v in enumerate(targets, 1):
            try:
                d = self.video_detail(v["bvid"])
                v["like"] = d["like"]
                v["play"] = d["view"] if d["view"] is not None else v["play"]
                v["reply"] = d["reply"]
            except BiliError as exc:
                v["like"] = None
                v["error"] = str(exc)
            if progress:
                progress(i, len(targets))
            if i < len(targets):
                self._sleep()
        return videos
