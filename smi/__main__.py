#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口。

    python3 -m smi login                     # 扫码登录（拿到凭据后可采全量）
    python3 -m smi bili <主页网址> --out x.xlsx   # 列出视频的播放量与点赞量
    python3 -m smi bili-video <bvid>         # 单条视频详情（无需登录态）
"""

import argparse
import json
import socket
import sys
import time
from pathlib import Path

from .auth import (
    CREDENTIAL_FILE, LoginError, POLL_CODES, check_login, clear_credentials,
    load_credentials, qrcode_login, render_qr_terminal, save_credentials,
    write_qr_file,
)
from .bili import (
    BiliClient, BiliError, NeedLogin, load_cookie, parse_bvid, parse_mid,
)
from .douyin import (
    DouyinClient, DouyinError, NeedAuthorize,
    authorize_url, exchange_code, load_config as load_dy_config, new_state,
    parse_redirect, save_config as save_dy_config,
)
from .browser_base import BrowserError, NeedLogin as BrowserNeedLogin
from .xhs_browser import (
    LOGIN_URL as XHS_LOGIN_URL, NOTE_MANAGE_URL as XHS_NOTE_URL,
    PROFILE_DIR as XHS_PROFILE, XhsBrowser, XhsCollector,
)
from .bili_browser import BiliBrowser
from .channels_browser import (
    PLATFORM_URL as CH_PLATFORM, PROFILE_DIR as CH_PROFILE,
    ChannelsBrowser, ChannelsCollector,
)
from .douyin_browser import (
    PROFILE_DIR as DY_PROFILE, DouyinBrowser, is_public, parse_export_file,
)

# 抖音导出表里比播放/点赞有价值得多的字段，一并写进 Excel
DY_EXTRA_COLUMNS = [
    ("审核状态", "status"), ("体裁", "genre"),
    ("完播率", "finish_rate"), ("5s完播率", "finish_rate_5s"),
    ("封面点击率", "cover_ctr"), ("2s跳出率", "bounce_2s"),
    ("平均播放时长", "avg_play_time"),
    ("主页访问量", "profile_visits"), ("粉丝增量", "fans_delta"),
]
from .export import write_csv, write_xlsx
from .fmt import fmt_int, fmt_rate, fmt_ts, pad

DY_HINT = """
抖音需要先在 open.douyin.com 注册一个开发者应用，然后授权一次。
────────────────────────────────────────────────────────────────────
     1. 打开 https://open.douyin.com ，创建「网站应用」，拿到 client_key / client_secret
     2. 在应用的「授权回调域」里填一个你控制的地址（本地调试可用带端口的 localhost）
     3. 授权一次：

          python3 -m smi dy-login --client-key <KEY> --client-secret <SECRET> \
                  --redirect-uri <你的回调地址>

     4. 之后拉数据：

          python3 -m smi dy --out 抖音数据.xlsx

⚠️ client_secret 等同于应用凭据，只存本地 data/credentials/douyin.json（权限 600）。
────────────────────────────────────────────────────────────────────
"""

DY_CREATOR_HINT = """
抖音需要先登录一次（扫码）。
────────────────────────────────────────────────────────────────────
  python3 -m smi dy-login        # 开浏览器窗口，用抖音 App 扫
  python3 -m smi dy --out 抖音数据.xlsx

服务器上没屏幕：加 --qr-out 把二维码存成图片再 scp 回本地扫。
登录态存在浏览器用户目录里，之后一直无头跑。
────────────────────────────────────────────────────────────────────
"""
LOGIN_HINT = """
需要登录态。
────────────────────────────────────────────────────────────────────
在运行本程序的机器上跑一次扫码登录即可：

    python3 -m smi login

凭据写入 data/credentials/bilibili.json（权限 600，不进版本库），
之后所有命令自动使用它。python3 -m smi whoami 可检查是否仍然有效。

不想登录的话，可以加 --source search 走匿名搜索接口（覆盖约 95%）。
────────────────────────────────────────────────────────────────────
"""

WIDTH = 60


# --------------------------------------------------------------------------- #
# 终端输出小工具
# --------------------------------------------------------------------------- #
def _status(message):
    """在行内刷新进度（stderr，不污染 stdout 的表格和导出）。"""
    sys.stderr.write("\r  %s" % message.ljust(WIDTH))
    sys.stderr.flush()


def _clear_status():
    sys.stderr.write("\r" + " " * (WIDTH + 4) + "\r")
    sys.stderr.flush()


def _page_progress(page, got, total):
    _status("读取作品列表 第 %d 页（已获取 %d/%s 条）"
            % (page, got, total if total is not None else "?"))


def _detail_progress(cur, total):
    _status("补齐点赞量 %d/%d" % (cur, total))


def _print_table(videos):
    columns = [("序号", 5), ("播放量", 10), ("点赞量", 10),
               ("点赞率", 8), ("发布日期", 11), ("标题", 0)]
    print("  ".join(pad(title, width) for title, width in columns))
    print("-" * 96)
    for i, v in enumerate(videos, 1):
        print("  ".join([
            pad(i, 5),
            pad(fmt_int(v.get("play")), 10),
            pad(fmt_int(v.get("like")), 10),
            pad(fmt_rate(v.get("like"), v.get("play")), 8),
            pad(fmt_ts(v.get("created")), 11),
            str(v.get("title", ""))[:46],
        ]))


def _write_out(videos, path, mid=None, total=None, id_header="bvid",
               account_label="账号 mid", extra_columns=None, stats_videos=None):
    """按扩展名选格式：.xlsx → Excel，其余 → CSV。"""
    if path.lower().endswith((".xlsx", ".xlsm")):
        try:
            write_xlsx(videos, path, mid=mid, total=total,
                       id_header=id_header, account_label=account_label,
                       extra_columns=extra_columns, stats_videos=stats_videos)
        except BiliError as exc:
            print("\n导出 Excel 失败：%s" % exc, file=sys.stderr)
            return None
        print("\n已导出 Excel：%s" % path)
    else:
        write_csv(videos, path, id_header=id_header)
        print("\n已导出 CSV：%s" % path)
    return path


# --------------------------------------------------------------------------- #
# bili：采集某账号的作品数据
# --------------------------------------------------------------------------- #
def _collect(client, mid, name, source, args, official):
    """按指定路径采集。返回 (videos, 是否需要逐条补齐点赞量)。"""
    if source == "login":
        videos, _ = client.space_videos(
            mid, max_pages=args.max_pages, progress=_page_progress)
        # 登录态的投稿列表接口不返回点赞量，必须逐条补
        return videos, True

    print("采集方式：搜索接口 + 游客身份（不需要登录态）")
    videos, _ = client.search_account_videos(
        mid, name=name, max_pages=args.max_pages,
        expect=official, progress=_page_progress)
    # 搜索结果已经带 play/like，不需要再逐条请求
    return videos, False


def _report_coverage(videos, official, client):
    if not official:
        return
    cover = len(videos) / official * 100
    print("\n覆盖率：%d/%d = %.0f%%" % (len(videos), official, cover))
    if cover >= 100:
        return
    missing = official - len(videos)
    if getattr(client, "last_search_truncated", False):
        print("  ⚠️ 少 %d 条，且是被翻页上限截断的——这份结果不完整。" % missing)
        print("     该账号名字比较通用，搜索结果里混有大量别人的视频。")
        print("     可以加大翻页：--max-pages 100；或登录后加 --source login 拿全量。")
    else:
        print("  少 %d 条。搜索接口不收录的视频（充电专属、被限流等）拿不到。" % missing)
        print("  要 100% 覆盖：python3 -m smi login 后加 --source login。")


def cmd_bili(args):
    mid = parse_mid(args.url_or_mid)
    if not mid:
        print("无法从 %r 解析出 mid，请传入形如 https://space.bilibili.com/123456 的网址"
              % args.url_or_mid, file=sys.stderr)
        return 2

    cookie = load_cookie(args.cookie)
    client = BiliClient(cookie=cookie, delay=(args.min_delay, args.max_delay))

    name = args.name or client.account_name(mid)
    official = client.account_video_count(mid)
    print("账号：%s (mid=%s)" % (name or "未知", mid))
    if official is not None:
        print("B站官方作品计数：%s" % official)
    print()

    source = args.source
    if source == "auto":
        source = "login" if cookie else "search"
    if source == "login" and not cookie:
        _clear_status()
        print(LOGIN_HINT, file=sys.stderr)
        return 2
    if source == "search" and not name:
        _clear_status()
        print("未能自动获取账号昵称（B站 card 接口有概率性风控，重试后仍失败）。\n"
              "搜索方案必须用昵称当关键词，拿 mid 是搜不到的，因此中止。\n\n"
              "解决办法（任选其一）：\n"
              "  1. 手动指定昵称：python3 -m smi bili %s --name \"账号昵称\"\n"
              "  2. 过几分钟重跑，风控窗口过去后会自动成功并写入缓存\n"
              "  3. 登录后走 --source login" % args.url_or_mid, file=sys.stderr)
        return 2

    try:
        videos, need_detail = _collect(client, mid, name, source, args, official)
    except NeedLogin as exc:
        _clear_status()
        print("\n%s" % exc, file=sys.stderr)
        print(LOGIN_HINT, file=sys.stderr)
        return 2
    except BiliError as exc:
        _clear_status()
        print("采集失败：%s" % exc, file=sys.stderr)
        if source == "login":
            print("提示：可以改用 --source search 走匿名搜索接口。", file=sys.stderr)
        return 1

    _clear_status()
    if not videos:
        print("没有取到任何视频。")
        return 0

    print("本次获取 %d 个视频%s\n"
          % (len(videos), "，仅补齐前 %d 条" % args.limit if args.limit else ""))

    if need_detail and not args.skip_detail:
        try:
            client.fill_likes(videos, limit=args.limit, progress=_detail_progress)
        except KeyboardInterrupt:
            sys.stderr.write("\n已中断，下面显示已补齐的部分。\n")
        _clear_status()

    videos.sort(key=lambda v: (v.get("created") or 0), reverse=True)

    print("── 全部视频（按发布时间倒序）")
    _print_table(videos)

    ranked = sorted((v for v in videos if v.get("play") is not None),
                    key=lambda v: v["play"], reverse=True)
    if ranked:
        print("\n── 播放量 Top 10")
        _print_table(ranked[:10])
        plays = sorted(v["play"] for v in ranked)
        print("\n播放量中位数 %s（%s 条）；中位数比平均数更抗爆款干扰。"
              % (fmt_int(plays[len(plays) // 2]), len(plays)))

    _report_coverage(videos, official, client)

    if args.out:
        _write_out(videos, args.out, mid=mid,
                   total=official if official is not None else len(videos))
    return 0


def cmd_bili_video(args):
    bvid = parse_bvid(args.bvid_or_url)
    if not bvid:
        print("无法解析 bvid：%r" % args.bvid_or_url, file=sys.stderr)
        return 2

    client = BiliClient(cookie=load_cookie(args.cookie), delay=(0, 0))
    try:
        d = client.video_detail(bvid)
    except BiliError as exc:
        print("查询失败：%s" % exc, file=sys.stderr)
        return 1

    print(d["title"])
    print("  UP主    %s (mid=%s)" % (d["owner"], d["mid"]))
    print("  发布    %s" % fmt_ts(d["created"]))
    print("  播放量  %s" % fmt_int(d["view"]))
    print("  点赞量  %s" % fmt_int(d["like"]))
    print("  点赞率  %s" % fmt_rate(d["like"], d["view"]))
    print("  评论 %s   收藏 %s   投币 %s   转发 %s"
          % (d["reply"], d["favorite"], d["coin"], d["share"]))
    return 0


# --------------------------------------------------------------------------- #
# login / whoami / logout
# --------------------------------------------------------------------------- #
def cmd_login(args):
    """B站扫码登录。

    默认**打开浏览器窗口**扫码，和抖音/视频号/小红书 保持一致。
    加 --terminal 则把二维码渲染在终端里（不需要浏览器，适合服务器）。

    两种方式产出的凭据完全一样，后续采集都走纯 HTTP 接口。
    """
    if args.terminal:
        return _bili_login_via_terminal(args)
    if not args.browser:
        # 没装 Playwright 就退回终端二维码，而不是直接报错
        try:
            from .browser_base import require_playwright
            require_playwright()
        except Exception:  # noqa: BLE001
            print("未安装 Playwright，改用终端二维码。")
            print("（想要浏览器窗口：python3 -m pip install playwright）\n")
            return _bili_login_via_terminal(args)
    return _bili_login_via_browser(args)


def _bili_login_via_browser(args):
    try:
        with BiliBrowser(profile_dir=args.profile, headless=False) as b:
            b.open()
            print("正在打开 B站首页...")
            b.goto()
            if b.is_logged_in():
                print("✅ 已经是登录状态。")
            else:
                print("已打开浏览器窗口，正在弹出登录二维码...\n")
                b.open_login_dialog()
                sys.stdout.flush()

                def on_wait(left):
                    _status("等待扫码... 剩余 %d 秒" % left)

                b.wait_for_login(timeout=args.timeout, on_wait=on_wait)
                _clear_status()
                print("✅ 扫码成功")

            cookie = b.cookie_string()
            names = b.cookie_names()
    except BrowserError as exc:
        _clear_status()
        print("浏览器登录失败：%s\n" % exc, file=sys.stderr)
        print("也可以不走浏览器，直接用终端二维码：python3 -m smi login", file=sys.stderr)
        return 1

    if "SESSDATA" not in cookie:
        print("没拿到 SESSDATA，登录可能没真正完成。", file=sys.stderr)
        print("（当前 Cookie：%s）" % ", ".join(names)[:150], file=sys.stderr)
        return 1

    info = check_login(BiliClient(cookie=cookie, delay=(0, 0)))
    path = save_credentials(cookie, mid=info.get("mid"), name=info.get("name"),
                            path=args.path or CREDENTIAL_FILE)
    print("   账号：%s (mid=%s)" % (info.get("name"), info.get("mid")))
    print("   凭据已写入：%s（权限 600，不进版本库）" % path)
    print("   采集仍然走纯 HTTP，比浏览器快得多：")
    print("       python3 -m smi bili %s --out 视频数据.xlsx" % (info.get("mid") or "<mid>"))
    return 0


def _bili_login_via_terminal(args):
    """原来的终端二维码流程，保持不动。"""

    """扫码登录。部署到新环境时唯一需要人工做的一步。"""
    def on_qr(url):
        # 路径一：导出成文件，scp 回本地用图形界面打开。服务器/无宽终端时用这个。
        if args.qr_out:
            try:
                path = write_qr_file(url, args.qr_out)
            except LoginError as exc:
                print("导出二维码失败：%s" % exc, file=sys.stderr)
                sys.stdout.flush()
                return
            print("二维码已写入：%s\n" % path)
            print("在本地机器上执行下面这行取回来，然后用浏览器打开 .svg 即可：")
            print("   scp %s:%s ./bili-qr.svg && open ./bili-qr.svg" % (socket.gethostname(), path))
            print("\n二维码 180 秒内有效，请尽快扫描。")
            sys.stdout.flush()
            return

        # 路径二：直接在终端渲染（SSH 进来时通常可用，宽度会自动适配）
        art, mode = render_qr_terminal(url)
        if art:
            print("用手机上的 B站 App 扫描下面的二维码，并在手机上确认：\n")
            print(art)
        else:
            print("无法在终端渲染二维码：%s\n" % mode)
            print("两个办法：")
            print("  1) 导出成图片，scp 回本地打开（推荐）：")
            print("       python3 -m smi login --qr-out bili-qr.svg")
            print("  2) 用手机浏览器直接打开下面这个链接并确认登录：")
            print("       %s" % url)
        print()
        sys.stdout.flush()   # 二维码走 stdout、进度走 stderr，不冲刷顺序会乱

    def on_status(code):
        _status("等待扫码... %s" % POLL_CODES.get(code, "未知(%s)" % code))

    try:
        cookie, _ = qrcode_login(
            timeout=args.timeout, on_qr=on_qr, on_status=on_status)
    except LoginError as exc:
        sys.stdout.flush()
        _clear_status()
        print("登录失败：%s" % exc, file=sys.stderr)
        return 1

    _clear_status()

    # 立刻验一次：确认凭据真能用，并顺带拿到昵称和 mid
    info = check_login(BiliClient(cookie=cookie, delay=(0, 0)))
    if not info.get("ok"):
        print("警告：拿到凭据但校验未通过（%s）" % info.get("message"), file=sys.stderr)

    path = save_credentials(cookie, mid=info.get("mid"), name=info.get("name"),
                            path=args.path or CREDENTIAL_FILE)
    print("✅ 登录成功")
    if info.get("ok"):
        print("   账号：%s (mid=%s)" % (info.get("name"), info.get("mid")))
    print("   凭据已写入：%s（权限 600，不进版本库）" % path)
    print("\n下次采集会自动使用它：")
    print("   python3 -m smi bili %s --out 视频数据.xlsx"
          % (info.get("mid") or "<你的mid>"))
    return 0


def cmd_whoami(args):
    """查看凭据状态。退出码非 0 即可当作健康检查。"""
    cookie = load_cookie(args.cookie)
    if not cookie:
        print("未找到任何凭据。", file=sys.stderr)
        print("运行一次扫码登录即可：python3 -m smi login", file=sys.stderr)
        return 2

    info = check_login(BiliClient(cookie=cookie, delay=(0, 0)))
    if not info.get("ok"):
        print("❌ 凭据无效：%s" % info.get("message"))
        print("   B站登录态会过期，重新扫码即可：python3 -m smi login")
        return 2

    print("✅ 已登录：%s (mid=%s)" % (info.get("name"), info.get("mid")))
    cred = load_credentials()
    if cred and cred.get("login_at"):
        days = (int(time.time()) - cred["login_at"]) / 86400.0
        print("   凭据写入于 %.1f 天前（%s）" % (days, CREDENTIAL_FILE))
    return 0


def cmd_logout(args):
    print("已删除本地凭据。" if clear_credentials() else "本地没有凭据文件。")
    return 0


# --------------------------------------------------------------------------- #
# 抖音（官方开放平台）
# --------------------------------------------------------------------------- #
DEFAULT_SCOPES = ["user_info", "video.list", "video.data"]


def _wait_for_callback(port, timeout=180):
    """起一个本地小服务接住 OAuth 回调。仅当回调地址指向本机时可用。"""
    import urllib.parse as up
    from http.server import BaseHTTPRequestHandler, HTTPServer

    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured.update(dict(up.parse_qsl(up.urlparse(self.path).query)))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>授权完成，可以关闭本页回到终端</h2>".encode("utf-8"))

        def log_message(self, *args):   # 静音
            pass

    srv = HTTPServer(("127.0.0.1", port), Handler)
    srv.timeout = timeout
    _status("等待浏览器回调（127.0.0.1:%d）..." % port)
    srv.handle_request()
    _clear_status()
    return captured


def cmd_dy_oauth(args):
    cfg = load_dy_config()
    if args.client_key:
        cfg["client_key"] = args.client_key
    if args.client_secret:
        cfg["client_secret"] = args.client_secret
    if args.redirect_uri:
        cfg["redirect_uri"] = args.redirect_uri
    if args.scopes:
        cfg["scopes"] = [s.strip() for s in args.scopes.split(",") if s.strip()]
    cfg.setdefault("scopes", DEFAULT_SCOPES)

    missing = [k for k in ("client_key", "client_secret", "redirect_uri") if not cfg.get(k)]
    if missing:
        print("缺少配置：%s\n" % "、".join(missing), file=sys.stderr)
        print(DY_HINT, file=sys.stderr)
        return 2

    save_dy_config(cfg)
    state = new_state()
    url = authorize_url(cfg["client_key"], cfg["redirect_uri"], cfg["scopes"], state)

    print("在浏览器里打开下面这个链接，登录抖音并点「授权」：\n")
    print("  %s\n" % url)
    print("已申请的权限（scope）：%s" % ", ".join(cfg["scopes"]))
    print("若授权页提示 scope 无效，说明该能力还没在 open.douyin.com 后台申请通过。\n")
    sys.stdout.flush()

    code = args.code
    got_state = None
    if not code and args.listen:
        params = _wait_for_callback(args.listen, timeout=args.timeout)
        code, got_state = params.get("code"), params.get("state")
        if params.get("error"):
            print("授权被拒绝：%s" % params.get("error"), file=sys.stderr)
            return 1

    if not code:
        print("浏览器会跳转到一个回调地址（可能打不开，没关系）。")
        print("把地址栏里那一整条网址粘贴到这里，回车：")
        try:
            raw = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。", file=sys.stderr)
            return 130
        code, got_state = parse_redirect(raw)

    if not code:
        print("没解析出 code，请检查粘贴的内容。", file=sys.stderr)
        return 2
    if got_state and got_state != state:
        print("警告：回调里的 state 与本次不一致，可能不是同一次授权请求。", file=sys.stderr)

    try:
        data = exchange_code(cfg["client_key"], cfg["client_secret"], code)
    except DouyinError as exc:
        print("换取令牌失败：%s" % exc, file=sys.stderr)
        return 1

    client = DouyinClient(cfg)
    client._absorb_token(data)
    save_dy_config(client.config)

    print("\n✅ 授权成功")
    print("   open_id          %s" % client.open_id)
    print("   access_token 有效期 约 %.0f 天"
          % ((client.config.get("expires_at", 0) - time.time()) / 86400.0))
    if client.config.get("scope"):
        print("   实际获批 scope    %s" % client.config["scope"])
    try:
        info = client.user_info()
        print("   账号             %s" % json.dumps(info, ensure_ascii=False)[:120])
    except DouyinError as exc:
        print("   （拿用户信息失败：%s）" % exc, file=sys.stderr)
    print("\n接下来：python3 -m smi dy --out 抖音数据.xlsx")
    return 0


def _dy_collect_open_platform(args, cfg):
    client = DouyinClient(cfg)

    def progress(page, got):
        _status("读取作品列表 第 %d 页（已获取 %d 条）" % (page, got))

    try:
        videos, total = client.video_list(max_pages=args.max_pages, progress=progress)
    except NeedAuthorize as exc:
        _clear_status()
        print("授权已失效：%s\n" % exc, file=sys.stderr)
        print(DY_HINT, file=sys.stderr)
        return 2
    except DouyinError as exc:
        _clear_status()
        print("采集失败：%s" % exc, file=sys.stderr)
        return 1
    finally:
        save_dy_config(client.config)     # 令牌可能刚被刷新
    return _dy_report(args, videos, client.config.get("open_id"), "open_id",
                      "抖音作品（开放平台）")


def _dy_report(args, videos, account_id, id_header, title):
    _clear_status()
    if not videos:
        print("没有取到任何作品。")
        return 0

    public = [v for v in videos if is_public(v)]
    hidden = [v for v in videos if not is_public(v)]

    print("%s：%d 条%s\n" % (title, len(videos),
                            "（仅前 %d 页）" % args.max_pages if args.max_pages else ""))
    if hidden:
        print("其中 %d 条是「自见/私密」，抖音不计播放量（导出里就是 0），"
              "下面的统计已排除它们。\n" % len(hidden))

    videos.sort(key=lambda v: (v.get("created") or 0), reverse=True)
    print("── 全部作品（按发布时间倒序）")
    _print_table(videos)

    ranked = sorted((v for v in public if v.get("play") is not None),
                    key=lambda v: v["play"], reverse=True)
    if ranked:
        print("\n── 播放量 Top 10（仅公开作品）")
        _print_table(ranked[:10])
        plays = sorted(v["play"] for v in ranked)
        likes = sorted(v["like"] for v in ranked if v.get("like") is not None)
        rates = sorted(r for r in
                       (v["like"] / v["play"] for v in ranked
                        if v.get("play") and v.get("like")) if r)
        print("\n公开作品 %d 条：播放量中位数 %s，点赞量中位数 %s%s"
              % (len(ranked), fmt_int(plays[len(plays) // 2]),
                 fmt_int(likes[len(likes) // 2]) if likes else "-",
                 "，点赞率中位数 %.2f%%" % (rates[len(rates) // 2] * 100) if rates else ""))
        print("（中位数比平均数更抗爆款干扰；非公开作品已排除）")
    elif hidden:
        print("\n所有作品都是非公开状态，没有可比较的播放数据。")

    if args.out:
        extra = DY_EXTRA_COLUMNS if any(v.get("finish_rate") is not None
                                        or v.get("status") for v in videos) else None
        _write_out(videos, args.out, mid=account_id, total=len(videos),
                   id_header=id_header, account_label="open_id" if account_id else "账号",
                   extra_columns=extra, stats_videos=public)
    return 0


def cmd_dy(args):
    """按手头有的凭据选路：浏览器登录态优先，其次开放平台 token。"""
    if args.from_file:
        return _dy_from_files(args.from_file, args)

    source = args.source
    if source == "auto":
        if (args.profile or DY_PROFILE).exists() or Path(args.profile or DY_PROFILE).exists():
            source = "browser"
        elif load_dy_config().get("access_token"):
            source = "open"
        else:
            source = "browser"      # 都没配置时也走浏览器，好给出「去登录」的提示

    if source == "browser":
        return _dy_collect_browser(args)
    if source == "open":
        return _dy_collect_open_platform(args, load_dy_config())

    print("还没有任何抖音凭据。\n", file=sys.stderr)
    print(DY_CREATOR_HINT, file=sys.stderr)
    return 2


def cmd_dy_login(args):
    """浏览器扫码登录。登录态存在浏览器用户目录里，之后一直复用。"""
    save_qr = args.qr_out
    headless = bool(save_qr) and not args.headful

    try:
        with DouyinBrowser(profile_dir=args.profile, headless=headless,
                           slow_mo=args.slow_mo) as b:
            b.open()
            b.goto()
            if b.is_logged_in():
                print("✅ 已经是登录状态，无需扫码。")
                print("   用户目录：%s" % (args.profile or DY_PROFILE))
                return 0

            if headless:
                png = b.grab_login_qr(save_to=save_qr)
                print("抖音登录二维码已保存：%s（%d 字节）\n" % (save_qr, len(png)))
                print("在本地机器上取回并打开：")
                print("   scp %s:%s ./dy-qr.png && open ./dy-qr.png"
                      % (socket.gethostname(), save_qr))
                print("\n用抖音 App 扫描，%d 秒内有效。\n" % args.timeout)
            else:
                print("已打开浏览器窗口，请用抖音 App 扫描窗口里的二维码。\n")
            sys.stdout.flush()

            def on_wait(left):
                _status("等待扫码... 剩余 %d 秒" % left)

            b.wait_for_login(timeout=args.timeout, on_wait=on_wait)
            _clear_status()
            print("✅ 登录成功")
            print("   登录态已存进浏览器用户目录：%s" % (args.profile or DY_PROFILE))
            print("\n接下来：python3 -m smi dy --out 抖音数据.xlsx")
            return 0
    except BrowserNeedLogin as exc:
        _clear_status()
        print("%s" % exc, file=sys.stderr)
        return 1
    except BrowserError as exc:
        _clear_status()
        print("登录失败：%s" % exc, file=sys.stderr)
        return 1


def _dy_from_files(paths, args):
    """直接解析已下载的导出文件（不启动浏览器）。"""
    videos = []
    for path in paths:
        try:
            got = parse_export_file(path)
        except BrowserError as exc:
            print("解析 %s 失败：%s" % (path, exc), file=sys.stderr)
            return 1
        print("   %s → %d 条" % (path, len(got)))
        videos.extend(got)
    return _dy_report(args, videos, None, "item_id", "抖音作品（导入的导出文件）")


def _dy_collect_browser(args):
    """启动无头浏览器，点官方「导出」按钮，下载并解析。"""
    try:
        with DouyinBrowser(profile_dir=args.profile, headless=True) as b:
            b.open()
            _status("正在打开创作者中心...")
            b.goto()
            _clear_status()
            if not b.is_logged_in():
                print("创作者中心未登录。先跑一次：\n", file=sys.stderr)
                print("    python3 -m smi dy-login\n", file=sys.stderr)
                print("（本地会打开浏览器窗口直接扫；服务器上加 --qr-out 导出二维码）",
                      file=sys.stderr)
                return 2
            _status("正在点击「导出」并等待下载...")
            files = b.export_videos()
            _clear_status()
            print("已下载 %d 个导出文件：" % len(files))
    except BrowserError as exc:
        _clear_status()
        print("采集失败：%s" % exc, file=sys.stderr)
        return 1
    return _dy_from_files(files, args)


def cmd_channels_login(args):
    """微信扫码登录视频号助手。

    默认**开有头窗口**——扫码必须看得见窗口。服务器上没屏幕时用 --headless
    配合 --qr-out 把二维码存成图片，再 scp 回本地看。
    """
    save_qr = args.qr_out
    headless = args.headless or bool(save_qr)

    try:
        with ChannelsBrowser(profile_dir=args.profile, headless=headless,
                             slow_mo=args.slow_mo) as b:
            b.open()
            print("正在打开视频号助手...")
            b.goto(CH_PLATFORM, settle=6)

            if b.is_logged_in():
                print("✅ 已经是登录状态，无需扫码。")
                print("   用户目录：%s" % (args.profile or CH_PROFILE))
                return 0

            b.open_login()
            if save_qr:
                png = b.grab_login_qr(save_to=save_qr)
                if png:
                    print("二维码已保存：%s（%d 字节）\n" % (save_qr, len(png)))
                    print("在本地取回并打开：")
                    print("   scp %s:%s ./channels-qr.png && open ./channels-qr.png"
                          % (socket.gethostname(), save_qr))
                    print("\n用**微信**扫描，%d 秒内有效。\n" % args.timeout)
                else:
                    print("没能从页面里取出二维码。请改用有头模式（去掉 --headless）。",
                          file=sys.stderr)
                    return 1
            else:
                # 顺手报告二维码有没有真的渲染出来，省得用户对着空白窗口发呆
                png = b.grab_login_qr()
                if png:
                    print("已打开浏览器窗口，请用**微信**扫描窗口里的二维码。\n")
                else:
                    print("已打开浏览器窗口，但暂时取不到二维码。")
                    print("如果窗口里显示「加载失败，点击重试」，点一下重试即可。\n")
            sys.stdout.flush()

            def on_wait(left):
                _status("等待微信扫码... 剩余 %d 秒" % left)

            b.wait_for_login(timeout=args.timeout, on_wait=on_wait)
            _clear_status()
            print("✅ 登录成功，登录态已存进 %s" % (args.profile or CH_PROFILE))
            print("\n接下来：python3 -m smi channels --out 视频号数据.xlsx")
            return 0
    except BrowserError as exc:
        _clear_status()
        print("登录失败：%s\n" % exc, file=sys.stderr)
        print(CH_HINT, file=sys.stderr)
        return 1


def cmd_channels(args):
    try:
        with ChannelsBrowser(profile_dir=args.profile, headless=True) as b:
            b.open()
            _status("正在打开视频号助手...")
            b.goto(CH_PLATFORM, settle=6)
            _clear_status()
            if not b.is_logged_in():
                print("视频号助手未登录。\n", file=sys.stderr)
                print(CH_HINT, file=sys.stderr)
                return 2

            def progress(label, page, got, total):
                _status("%s列表 第 %d 页（已获取 %s/%s 条）"
                        % (label, page, got, total if total is not None else "?"))

            collector = ChannelsCollector(b)
            _status("正在读取作品列表...")
            works, errors = collector.collect(max_pages=args.max_pages,
                                              on_progress=progress)
            _clear_status()
            if errors:
                print("有 %d 个响应解析失败（多半是接口结构变了）：%s"
                      % (len(errors), errors[:3]), file=sys.stderr)
    except BrowserError as exc:
        _clear_status()
        print("采集失败：%s" % exc, file=sys.stderr)
        return 1

    if not works:
        print("没有取到任何作品。可能这个账号还没发过内容，或接口结构变了。")
        return 0

    works.sort(key=lambda v: (v.get("created") or 0), reverse=True)
    print("视频号作品：%d 条\n" % len(works))
    print("── 全部作品（按发布时间倒序）")
    _print_table(works)

    ranked = sorted((v for v in works if v.get("play") is not None),
                    key=lambda v: v["play"], reverse=True)
    if ranked:
        print("\n── 播放量 Top 10")
        _print_table(ranked[:10])
        plays = sorted(v["play"] for v in ranked)
        print("\n播放量中位数 %s（%s 条）" % (fmt_int(plays[len(plays) // 2]), len(plays)))

    if args.out:
        _write_out(works, args.out, total=len(works), id_header="object_id",
                   account_label="账号")

    if args.dump:
        out = Path(args.dump)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(works, ensure_ascii=False, indent=2), encoding="utf-8")
        print("原始数据已写入 %s（含你自己的数据，别提交）" % out)
    return 0


# --------------------------------------------------------------------------- #
# 小红书
# --------------------------------------------------------------------------- #
XHS_EXTRA_COLUMNS = [
    ("曝光数", "exposure"), ("封面点击率", "cover_ctr"),
    ("涨粉", "follow"), ("分享数", "share"),
    ("人均观看时长", "avg_watch_time"), ("弹幕", "danmaku"),
    ("完播率", "finish_rate"), ("2秒退出率", "bounce_2s"),
]

XHS_HINT = """
小红书需要先登录一次。
────────────────────────────────────────────────────────────────────
  1. 本地有屏幕（推荐）：

       python3 -m smi xhs-login

     会打开浏览器窗口。**小红书的登录是手机号 + 短信验证码**（不是扫码），
     在窗口里填手机号、收验证码、登录即可。登录态会存进浏览器用户目录。

  2. 登录后采集：

       python3 -m smi xhs --out 小红书数据.xlsx

  3. 服务器上没屏幕：在有屏幕的机器上登录一次，把 data/browser/xhs/
     整个目录拷过去，之后一直无头跑。登录过期了要重新来一次。
────────────────────────────────────────────────────────────────────
"""


def cmd_xhs_login(args):
    """小红书登录。必须在有头窗口里手动完成（短信验证码）。"""
    try:
        with XhsBrowser(profile_dir=args.profile, headless=False,
                        slow_mo=args.slow_mo) as b:
            b.open()
            print("正在打开小红书创作服务平台...")
            b.goto(XHS_LOGIN_URL, settle=6)

            if b.is_logged_in():
                print("✅ 已经是登录状态，无需重新登录。")
                return 0

            b.bring_to_front()
            print("已打开登录浏览器窗口（如果没看到，请查看 Dock 或任务栏中的浏览器）。")
            print("**小红书的登录是手机号 + 短信验证码**，不是扫码：")
            print("  1. 在窗口里填手机号")
            print("  2. 点「发送验证码」，去短信里拿 6 位数字")
            print("  3. 填进去点「登 录」")
            print("登录成功后这里会自动继续，不用回来按任何键。\n")
            sys.stdout.flush()

            def on_wait(left):
                _status("等待登录... 剩余 %d 秒" % left)

            # 登录成功后页面会跳到数据看板，这里直接判数据看板是否可见
            deadline = time.time() + args.timeout
            while time.time() < deadline:
                if b.is_logged_in():
                    break
                on_wait(int(deadline - time.time()))
                time.sleep(2)
            else:
                _clear_status()
                print("等待登录超时。重新运行即可再试一次。", file=sys.stderr)
                return 1

            _clear_status()
            b.goto(XHS_NOTE_URL, settle=5)
            print("✅ 登录成功，登录态已存进 %s" % (args.profile or XHS_PROFILE))
            print("\n接下来：python3 -m smi xhs --out 小红书数据.xlsx")
            return 0
    except BrowserError as exc:
        _clear_status()
        print("登录失败：%s\n" % exc, file=sys.stderr)
        print(XHS_HINT, file=sys.stderr)
        return 1


def cmd_xhs(args):
    try:
        with XhsBrowser(profile_dir=args.profile, headless=True) as b:
            b.open()
            _status("正在打开笔记管理...")
            b.goto(XHS_NOTE_URL, settle=8)
            _clear_status()
            if not b.is_logged_in():
                print("小红书未登录。\n", file=sys.stderr)
                print(XHS_HINT, file=sys.stderr)
                return 2

            def progress(page, got):
                _status("笔记列表 第 %d 页（已获取 %d 条）" % (page, got))

            notes, errors = XhsCollector(b).collect(max_pages=args.max_pages,
                                                    on_progress=progress)
            _clear_status()
            if errors:
                for e in errors[:3]:
                    print("  %s" % e, file=sys.stderr)
    except BrowserError as exc:
        _clear_status()
        print("采集失败：%s" % exc, file=sys.stderr)
        return 1

    if not notes:
        print("没有取到任何笔记。可能这个账号还没发过内容，或接口结构变了。")
        if args.dump:
            print("建议加 --dump 看看原始响应的字段名。")
        return 0

    notes.sort(key=lambda v: (v.get("created") or 0), reverse=True)
    print("小红书笔记：%d 条\n" % len(notes))
    print("── 全部笔记（按发布时间倒序）")
    _print_table(notes)

    ranked = sorted((v for v in notes if v.get("play") is not None),
                    key=lambda v: v["play"], reverse=True)
    if ranked:
        print("\n── 观看量 Top 10")
        _print_table(ranked[:10])
        plays = sorted(v["play"] for v in ranked)
        print("\n观看量中位数 %s（%s 条）" % (fmt_int(plays[len(plays) // 2]), len(plays)))

    if args.out:
        _write_out(notes, args.out, total=len(notes), id_header="note_id",
                   account_label="账号", extra_columns=XHS_EXTRA_COLUMNS,
                   stats_videos=ranked or None)

    if args.dump:
        out = Path(args.dump)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
        print("原始数据已写入 %s（含你自己的数据，别提交）" % out)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="smi", description="自媒体数据工具（脚本版）")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("bili", help="列出某主页全部视频的播放量与点赞量")
    b.add_argument("url_or_mid", help="主页网址或 mid")
    b.add_argument("--source", choices=["auto", "search", "login"], default="auto",
                   help="采集路径；auto=有凭据走 login，否则走 search（默认）")
    b.add_argument("--name", help="手动指定B站昵称，绕过自动获取（search 的关键词）")
    b.add_argument("--cookie", help="临时指定登录态，覆盖本地凭据")
    b.add_argument("--out", help="导出路径：.xlsx 存为 Excel，其余存为 CSV")
    b.add_argument("--limit", type=int, help="只补齐前 N 条的点赞量（快速试跑）")
    b.add_argument("--max-pages", type=int, help="最多翻多少页")
    b.add_argument("--skip-detail", action="store_true", help="跳过逐条补齐点赞量")
    b.add_argument("--min-delay", type=float, default=1.0, help="请求最小间隔秒数")
    b.add_argument("--max-delay", type=float, default=2.0, help="请求最大间隔秒数")
    b.set_defaults(func=cmd_bili)

    lg = sub.add_parser("login", help="扫码登录（部署到新环境时跑一次）")
    lg.add_argument("--timeout", type=int, default=180, help="等待扫码的秒数")
    lg.add_argument("--path", help="凭据写入路径")
    lg.add_argument("--browser", action="store_true",
                    help="（默认行为）打开浏览器窗口扫码")
    lg.add_argument("--terminal", action="store_true",
                    help="改用终端二维码，不需要浏览器（服务器/无 Playwright 时）")
    lg.add_argument("--profile", help="浏览器用户目录（--browser 时用）")
    lg.add_argument("--qr-out", metavar="FILE",
                    help="把二维码导出成文件（.svg 推荐 / .png 需 Pillow / .txt），"
                         "供 scp 回本地打开；服务器上终端太窄时用这个")
    lg.set_defaults(func=cmd_login)

    wa = sub.add_parser("whoami", help="查看当前登录态是否有效")
    wa.add_argument("--cookie", help="临时指定登录态")
    wa.set_defaults(func=cmd_whoami)

    sub.add_parser("logout", help="删除本地凭据").set_defaults(func=cmd_logout)

    dl = sub.add_parser("dy-oauth", help="抖音开放平台授权（OAuth，需注册应用）")
    dl.add_argument("--client-key", help="开发者应用的 client_key")
    dl.add_argument("--client-secret", help="开发者应用的 client_secret")
    dl.add_argument("--redirect-uri", help="应用里配置的授权回调地址")
    dl.add_argument("--scopes", help="逗号分隔，默认 %s" % ",".join(DEFAULT_SCOPES))
    dl.add_argument("--listen", type=int, metavar="PORT",
                    help="起本地服务自动接收回调（回调地址指向本机时用）")
    dl.add_argument("--code", help="已经拿到授权码时直接给，省去粘贴")
    dl.add_argument("--timeout", type=int, default=180, help="等待回调的秒数")
    dl.set_defaults(func=cmd_dy_oauth)

    dy = sub.add_parser("dy", help="拉取自己抖音账号的作品数据")
    dy.add_argument("--out", help="导出路径：.xlsx 存为 Excel，其余存为 CSV")
    dy.add_argument("--max-pages", type=int, help="最多取多少页")
    dy.add_argument("--cookie", help="临时指定创作者中心登录态")
    dy.add_argument("--source", choices=["auto", "browser", "open"], default="auto",
                    help="auto=有浏览器登录态走 browser，否则看开放平台 token")
    dy.add_argument("--profile", help="浏览器用户目录（登录态所在）")
    dy.add_argument("--from-file", action="append", metavar="FILE",
                    help="直接解析已下载的导出文件，不启动浏览器（可重复）")
    dy.set_defaults(func=cmd_dy)

    dbl = sub.add_parser("dy-login", help="抖音扫码登录（浏览器，推荐）")
    dbl.add_argument("--headful", action="store_true",
                     help="强制打开浏览器窗口（默认已是有头；用于覆盖 --qr-out）")
    dbl.add_argument("--qr-out", metavar="FILE",
                     help="无头模式：把二维码存成 PNG，供 scp 回本地扫")
    dbl.add_argument("--profile", help="浏览器用户目录")
    dbl.add_argument("--timeout", type=int, default=180, help="等待扫码的秒数")
    dbl.add_argument("--slow-mo", type=int, default=0, help="每步放慢毫秒数（调试用）")
    dbl.set_defaults(func=cmd_dy_login)

    chl = sub.add_parser("channels-login", help="视频号微信扫码登录")
    chl.add_argument("--headless", action="store_true",
                     help="不开窗口（服务器用）；配合 --qr-out 导出二维码")
    chl.add_argument("--qr-out", metavar="FILE",
                     help="把二维码存成 PNG，供 scp 回本地扫；指定后自动无头")
    chl.add_argument("--profile", help="浏览器用户目录")
    chl.add_argument("--timeout", type=int, default=180, help="等待扫码的秒数")
    chl.add_argument("--slow-mo", type=int, default=0, help="每步放慢毫秒数")
    chl.set_defaults(func=cmd_channels_login)

    ch = sub.add_parser("channels", help="采集自己视频号的作品数据")
    ch.add_argument("--out", help="导出路径：.xlsx 存为 Excel，其余存为 CSV")
    ch.add_argument("--profile", help="浏览器用户目录")
    ch.add_argument("--max-pages", type=int, default=10, help="每个列表最多翻多少页")
    ch.add_argument("--dump", help="把原始数据写成 JSON")
    ch.set_defaults(func=cmd_channels)

    xl = sub.add_parser("xhs-login", help="小红书登录（手机号+验证码，需有头窗口）")
    xl.add_argument("--profile", help="浏览器用户目录")
    xl.add_argument("--timeout", type=int, default=300, help="等待登录的秒数")
    xl.add_argument("--slow-mo", type=int, default=0, help="每步放慢毫秒数")
    xl.set_defaults(func=cmd_xhs_login)

    xh = sub.add_parser("xhs", help="采集自己小红书的笔记数据")
    xh.add_argument("--out", help="导出路径：.xlsx 存为 Excel，其余存为 CSV")
    xh.add_argument("--profile", help="浏览器用户目录")
    xh.add_argument("--max-pages", type=int, default=20, help="最多翻多少页")
    xh.add_argument("--dump", help="把归一化后的数据写成 JSON")
    xh.set_defaults(func=cmd_xhs)

    v = sub.add_parser("bili-video", help="查单条视频（无需登录态）")
    v.add_argument("bvid_or_url", help="bvid 或视频网址")
    v.add_argument("--cookie", help="可选，一般不填")
    v.set_defaults(func=cmd_bili_video)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
