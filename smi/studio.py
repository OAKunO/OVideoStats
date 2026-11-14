"""Local studio services. Every row comes from the existing smi collectors."""
import json
import hashlib
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .auth import load_credentials, save_credentials, check_login
from .bili import BiliClient, BiliError
from .bili_browser import BiliBrowser
from .browser_base import BrowserError
from .channels_browser import ChannelsBrowser, ChannelsCollector, PLATFORM_URL
from .douyin_browser import DouyinBrowser, is_public, parse_export_file
from .xhs_browser import XhsBrowser, XhsCollector, LOGIN_URL, NOTE_MANAGE_URL, LOGGED_OUT_MARKERS
from .export import write_selected_xlsx
from .avatars import browser_avatar, fetch_avatar
from .browser_selection import profile_browser_name

APP_DIR = Path('data/studio')
REGISTRY = APP_DIR / 'accounts.json'
PREFERENCES = APP_DIR / 'preferences.json'
PLATFORMS = {
    'bili': {'name': 'B站', 'login': '扫码登录'},
    'douyin': {'name': '抖音', 'login': '扫码登录'},
    'xhs': {'name': '小红书', 'login': '手机号 + 短信验证码'},
    'channels': {'name': '视频号', 'login': '微信扫码登录'},
}
# Only keys the collectors actually produce. Availability is refined after a collection.
FIELDS = {
    'bili': [('title','标题'),('created','发布日期'),('play','播放量'),('like','点赞量'),('like_rate','点赞率'),('comment','评论数'),('length','时长'),('bvid','视频 ID'),('url','链接')],
    'douyin': [('title','标题'),('created','发布日期'),('play','播放量'),('like','点赞量'),('like_rate','点赞率'),('status','审核状态'),('genre','体裁'),('finish_rate','完播率'),('finish_rate_5s','5 秒完播率'),('cover_ctr','封面点击率'),('bounce_2s','2 秒跳出率'),('avg_play_time','平均播放时长'),('share','分享数'),('collect','收藏数'),('comment','评论数'),('profile_visits','主页访问量'),('fans_delta','粉丝增量'),('item_id','作品 ID'),('url','链接')],
    'xhs': [('title','标题'),('created','发布日期'),('play','观看量'),('like','点赞量'),('like_rate','点赞率'),('comment','评论数'),('collect','收藏数'),('share','分享数'),('exposure','曝光数'),('cover_ctr','封面点击率'),('follow','涨粉'),('avg_watch_time','人均观看时长'),('danmaku','弹幕'),('finish_rate','完播率'),('bounce_2s','2 秒退出率'),('genre','类型'),('length','时长'),('item_id','笔记 ID'),('url','链接')],
    'channels': [('title','标题'),('created','发布日期'),('play','播放量'),('like','点赞量'),('like_rate','点赞率'),('comment','评论数'),('share','分享数'),('collect','收藏数'),('follow','涨粉'),('recommend','推荐数'),('finish_rate','完播率'),('bounce_2s','2 秒跳出率'),('avg_play_time','平均播放时长'),('item_id','作品 ID'),('url','链接')],
}
DEFAULT_FIELDS = ('title','created','play','like','like_rate','comment','url')
_lock = threading.RLock()


def _load():
    try:
        value = json.loads(REGISTRY.read_text(encoding='utf-8'))
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def _save(rows):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=REGISTRY.parent, prefix='.accounts-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, REGISTRY)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load_preferences():
    try:
        value = json.loads(PREFERENCES.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _update_preferences(changes):
    with _lock:
        value = {**_load_preferences(), **changes}
        PREFERENCES.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=PREFERENCES.parent, prefix='.preferences-', suffix='.json')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump(value, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, PREFERENCES)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def get_theme():
    with _lock:
        theme = _load_preferences().get('theme')
    return theme if theme in ('light', 'dark', 'system') else 'light'


def set_theme(theme):
    if theme not in ('light', 'dark', 'system'):
        raise ValueError('无效的主题选项')
    _update_preferences({'theme': theme})
    return theme


def get_account_sort():
    with _lock:
        value = _load_preferences().get('account_sort', {})
    if not isinstance(value, dict):
        value = {}
    return {'key': value.get('key') if value.get('key') in ('platform', 'connected_at', 'name') else 'connected_at',
            'direction': value.get('direction') if value.get('direction') in ('asc', 'desc') else 'asc'}


def set_account_sort(key, direction):
    if key not in ('platform', 'connected_at', 'name') or direction not in ('asc', 'desc'):
        raise ValueError('无效的账号排序选项')
    value = {'key': key, 'direction': direction}
    _update_preferences({'account_sort': value})
    return value


def get_export_folder():
    """Return the last folder chosen in this app, even if it is now unavailable."""
    with _lock:
        folder = _load_preferences().get('export_folder')
    return folder if isinstance(folder, str) else ''


def set_export_folder(folder):
    """Remember a user-selected existing directory across app restarts."""
    if not isinstance(folder, str) or not folder.strip():
        raise ValueError('请选择已经存在的导出文件夹')
    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        raise ValueError('所选文件夹不存在，请重新选择。')
    _update_preferences({'export_folder': str(path)})
    return str(path)


def _public(row):
    result = {key: row.get(key) for key in ('id','platform','identity','name','fields','include_private','last_export','available_fields','connected_at','last_checked_at','last_check_error')}
    result['connection_status'] = row.get('connection_status', 'unchecked')
    result['browser_name'] = profile_browser_name(row.get('profile'))
    result['avatar_url'] = f'/api/avatars/{row["id"]}?v={row["avatar_version"]}' if row.get('avatar_version') else None
    return result


def avatar_path(account_id):
    if not isinstance(account_id, str) or not re.fullmatch(r'[a-f0-9]{32}', account_id):
        return None
    row = get_account(account_id)
    path = APP_DIR / 'avatars' / f'{account_id}.png'
    return path if row and row.get('avatar_version') and path.is_file() else None


def _store_avatar(account_id, image):
    try:
        _write_avatar(account_id, image)
    except OSError:
        pass  # An optional image must not prevent connecting or exporting.


def _write_avatar(account_id, image):
    if not image:
        return
    with _lock:
        rows = _load()
        row = next((x for x in rows if x['id'] == account_id), None)
        if not row or not re.fullmatch(r'[a-f0-9]{32}', account_id):
            return
        directory = APP_DIR / 'avatars'
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix='.avatar-')
        try:
            with os.fdopen(fd, 'wb') as fh:
                fh.write(image)
            os.replace(tmp, directory / f'{account_id}.png')
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        row['avatar_version'] = hashlib.sha256(image).hexdigest()[:16]
        _save(rows)


def list_accounts():
    with _lock:
        return [_public(row) for row in _load()]


def get_account(account_id):
    with _lock:
        return next((row for row in _load() if row['id'] == account_id), None)


def field_options(platform):
    return [{'key': key, 'label': label} for key, label in FIELDS[platform]]


def pending_sessions():
    """Show legacy browser profiles that have not passed account verification."""
    with _lock:
        rows = _load()
    result = []
    for platform in ('douyin', 'xhs', 'channels'):
        legacy = Path('data/browser') / platform
        if (legacy / 'Default').exists() and not any(x['platform'] == platform for x in rows):
            result.append({'platform': platform, 'message': '本机有旧浏览器会话，尚未确认可采集。请检测或重新登录。'})
    return result


def _cookie_id(browser, platform):
    names = {
        'douyin': ('uid_tt','uid_tt_ss','passport_assist_user'),
        'xhs': ('user_id','userId','xhs_user_id'),
        'channels': ('finder_username','finderUsername'),
    }[platform]
    cookies = {c['name']: c['value'] for c in browser._ctx.cookies()}
    for name in names:
        if cookies.get(name):
            return str(cookies[name])
    return None


def _page_identity(browser, platform):
    """Read stable IDs from the authenticated page, never a device/session token."""
    hints = {
        'douyin': ('uid','user_id','sec_uid'),
        'xhs': ('user_id','userId','user_id_string'),
        'channels': ('finderUsername','finderUserName','username'),
    }[platform]
    try:
        result = browser.page.evaluate(r'''(keys) => {
          const values = [];
          const seen = new Set();
          const visit = (x, depth) => {
            if (!x || depth > 4 || typeof x !== 'object' || seen.has(x)) return;
            seen.add(x);
            for (const [key, value] of Object.entries(x)) {
              if (keys.includes(key) && (typeof value === 'string' || typeof value === 'number') && String(value).length > 3) values.push(String(value));
              else if (typeof value === 'object') visit(value, depth + 1);
            }
          };
          for (const key of Object.keys(localStorage)) {
            try { visit(JSON.parse(localStorage.getItem(key)), 0); } catch (_) {}
          }
          for (const selector of ['a[href*="user/"]','a[href*="profile/"]']) {
            for (const a of document.querySelectorAll(selector)) {
              const m = a.href.match(/(?:user|profile)\/([^/?#]+)/);
              if (m) values.push(m[1]);
            }
          }
          return values;
        }''', hints)
        return next((str(x) for x in result if x and x not in ('undefined','null')), None)
    except Exception:
        return None


def _channels_identity(browser):
    # Replay the site's own signed account-info request, including its body.
    # A bare POST to this endpoint returns an error even when Chrome has cookies.
    captured = []
    def on_request(request):
        if 'mmfinderassistant-bin/auth/get_auth_info' in request.url:
            captured.append(request)
    browser.page.on('request', on_request)
    try:
        browser.goto(PLATFORM_URL, settle=5)
        if not captured:
            return None
        request = captured[-1]
        headers = {key: value for key, value in request.headers.items()
                   if key.lower() not in ('host', 'content-length')}
        response = browser._ctx.request.post(request.url, data=request.post_data or '{}',
                                             headers=headers, timeout=15000)
        data = response.json()
        if data.get('errCode') not in (None, 0):
            return None
        def walk(obj, depth=0):
            if depth > 5 or not isinstance(obj, dict):
                return None
            for key in ('finderUsername','finderUserName','finder_username'):
                if obj.get(key):
                    return str(obj[key])
            for value in obj.values():
                found = walk(value, depth + 1)
                if found:
                    return found
            return None
        return walk(data)
    except Exception:
        return None
    finally:
        try:
            browser.page.remove_listener('request', on_request)
        except Exception:
            pass


def _display_name(browser, platform, identity):
    try:
        text = browser.page.evaluate('''() => {
          const selectors = ['[class*="user-name"]','[class*="userName"]','[class*="nickname"]','[class*="nick-name"]','[class*="avatar"] + span'];
          for (const s of selectors) { const t = document.querySelector(s)?.textContent?.trim(); if (t && t.length < 40) return t; }
          return '';
        }''')
        return text or f'{PLATFORMS[platform]["name"]} · {identity[:12]}'
    except Exception:
        return f'{PLATFORMS[platform]["name"]} · {identity[:12]}'


def _identity(browser, platform):
    if platform == 'channels' and not browser.is_logged_in():
        raise ValueError('视频号显示登录首页，当前会话尚未完成登录。')
    local_channels_id = None
    if platform == 'channels':
        try:
            local_channels_id = browser.page.evaluate('() => localStorage.getItem("finder_username")')
        except Exception:
            pass
    identity = local_channels_id or (_channels_identity(browser) if platform == 'channels' else None) or _cookie_id(browser, platform) or _page_identity(browser, platform)
    if not identity:
        raise ValueError('登录已完成，但无法从平台页面确认唯一账号 ID。请重试；为防止重复或混淆账号，本次未添加。')
    return identity, _display_name(browser, platform, identity)


def _registration_name(browser, platform, fallback):
    """Read Douyin's account switcher on its creator home during connection."""
    if platform != 'douyin':
        return fallback
    try:
        browser.goto('https://creator.douyin.com/creator-micro/home', settle=2)
        name = browser.page.locator('button[class*="account-trigger"] span[class*="name"]').first.inner_text(timeout=3000).strip()
        return name[:60] if name else fallback
    except Exception:
        return fallback


def _register(platform, identity, name, profile=None, credential=None, avatar=None):
    with _lock:
        rows = _load()
        if any(x['platform'] == platform and str(x['identity']) == str(identity) for x in rows):
            raise ValueError(f'{PLATFORMS[platform]["name"]}账号“{name}”已经添加过了。')
        row = {'id': uuid.uuid4().hex, 'platform': platform, 'identity': str(identity),
               'name': name, 'profile': str(profile) if profile else None,
               'credential': str(credential) if credential else None,
               'fields': [key for key, _ in FIELDS[platform] if key in DEFAULT_FIELDS],
               'include_private': False, 'last_export': None, 'available_fields': None,
               'connection_status': 'connected', 'connected_at': time.time(),
               'last_checked_at': time.time(), 'last_check_error': None}
        rows.append(row)
        _save(rows)
        _store_avatar(row['id'], avatar)
        return _public(get_account(row['id']))


def add_account(platform, update):
    if platform not in PLATFORMS:
        raise ValueError('不支持的平台')
    update('正在打开平台登录页面…')
    staging = APP_DIR / 'pending' / uuid.uuid4().hex
    staging.mkdir(parents=True)
    cls = {'bili': BiliBrowser, 'douyin': DouyinBrowser, 'xhs': XhsBrowser, 'channels': ChannelsBrowser}[platform]
    try:
        with cls(profile_dir=staging, headless=False) as browser:
            browser.open()
            if isinstance(getattr(browser, 'browser_message', None), str) and browser.browser_message:
                update(browser.browser_message)
            if platform == 'bili':
                browser.goto()
                if not browser.is_logged_in():
                    browser.open_login_dialog()
            elif platform == 'douyin':
                browser.goto()
            elif platform == 'xhs':
                browser.goto(LOGIN_URL)
            else:
                browser.open_login()
            if platform == 'channels' or not browser.is_logged_in():
                update('请扫码并在手机确认；等待期间不会刷新视频号二维码。若显示微信快捷登录，请选择“使用其他头像、昵称或账号”。' if platform == 'channels' else '请在打开的浏览器中完成登录；小红书使用短信验证码。')
                browser.wait_for_login(timeout=300)
            update('登录成功，正在核对账号身份…')
            if platform == 'bili':
                cookie = browser.cookie_string()
                info = check_login(BiliClient(cookie=cookie, delay=(0, 0)))
                if not info.get('ok') or not info.get('mid'):
                    raise ValueError('无法确认 B站账号身份，未添加。')
                identity, name = str(info['mid']), info.get('name') or f'B站 · {info["mid"]}'
                avatar = fetch_avatar(info.get('avatar'), browser)
            else:
                identity, name = _identity(browser, platform)
                name = _registration_name(browser, platform, name)
                avatar = browser_avatar(browser, platform)
        with _lock:
            if any(x['platform'] == platform and str(x['identity']) == identity for x in _load()):
                raise ValueError(f'{PLATFORMS[platform]["name"]}账号“{name}”已经添加过了。')
            target = APP_DIR / 'profiles' / platform / uuid.uuid4().hex
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staging), str(target))
            credential = None
            if platform == 'bili':
                credential = APP_DIR / 'credentials' / f'{uuid.uuid4().hex}.json'
                save_credentials(cookie, mid=identity, name=name, path=credential)
            return _register(platform, identity, name, target, credential, avatar)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


class LoginCheckFailure(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _verify_bili_session(row):
    path = row.get('credential')
    try:
        credential = load_credentials(path, strict=True) if path else None
    except FileNotFoundError:
        credential = None
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise LoginCheckFailure('error', '本机登录凭据文件损坏，无法确认登录状态，请重新连接。') from exc
    if not credential or not credential.get('cookie'):
        raise LoginCheckFailure('expired', '本机登录凭据缺失，请重新连接。')
    info = check_login(BiliClient(cookie=credential['cookie'], delay=(0, 0)))
    if not info.get('ok'):
        if info.get('code') == -101 or (info.get('code') == 0 and info.get('message') == '未登录'):
            raise LoginCheckFailure('expired', '平台确认当前未登录，请重新连接。')
        raise LoginCheckFailure('error', '无法确认 B站登录状态：' + (info.get('message') or '平台未返回有效结果'))
    if not info.get('mid'):
        raise LoginCheckFailure('error', '平台未返回账号 ID，无法确认登录身份。')
    return str(info['mid']), info.get('name') or row.get('name') or 'B站账号'


def _verify_browser_session(row):
    platform = row['platform']
    profile = row.get('profile')
    if not profile or not Path(profile).is_dir():
        raise LoginCheckFailure('expired', '本机登录会话缺失，请重新连接。')
    cls = {'douyin': DouyinBrowser, 'xhs': XhsBrowser, 'channels': ChannelsBrowser}[platform]
    url = {'douyin': None, 'xhs': NOTE_MANAGE_URL, 'channels': PLATFORM_URL}[platform]
    with cls(profile_dir=profile, headless=True) as browser:
        browser.open()
        browser.goto(url, settle=1) if url else browser.goto(settle=1)
        for _ in range(20):
            if browser.is_logged_in():
                try:
                    return _identity(browser, platform)
                except ValueError as exc:
                    raise LoginCheckFailure('error', '已看到平台后台，但无法确认账号 ID，请稍后重试或重新连接。') from exc
            browser.page.wait_for_timeout(250)
        # Only an explicit login screen means expired. An empty, blocked or
        # changed dashboard is an inconclusive check, never a successful check.
        text = browser.body_text(5000) if platform == 'channels' else browser.page.inner_text('body', timeout=5000)
        if any(marker in text for marker in ('安全验证', '访问受限', '操作频繁', '网络异常', '网络错误')):
            raise LoginCheckFailure('error', '平台显示验证或访问异常，暂时无法确认登录状态，请稍后重试。')
        if platform == 'xhs':
            logged_out = any(marker in text for marker in LOGGED_OUT_MARKERS)
        elif platform == 'douyin':
            logged_out = any(marker in text for marker in ('扫码登录', '验证码登录', '密码登录'))
        else:
            logged_out = (any(marker in browser.url.lower() for marker in ('login', 'passport', 'oauth-callback'))
                          or '微信扫码登录' in text
                          or ('一站式服务' in text and '登录视频号助手' in text)
                          or browser.count('text="登录"') > 0)
        if logged_out:
            raise LoginCheckFailure('expired', '平台显示登录页面，请重新连接。')
        raise LoginCheckFailure('error', '页面未显示可确认的登录状态，可能尚未加载完成或平台页面已变化，请重试。')


def _verify_session(row):
    identity, name = _verify_bili_session(row) if row['platform'] == 'bili' else _verify_browser_session(row)
    if row.get('identity') is not None and str(identity) != str(row['identity']):
        raise LoginCheckFailure('mismatch', '当前登录的是另一个账号，请重新连接原账号。')
    return identity, name


def _record_login_check(account_id, status, checked_at, error=None):
    with _lock:
        rows = _load()
        row = next((row for row in rows if row['id'] == account_id), None)
        if row:
            row.update(connection_status=status, last_checked_at=checked_at, last_check_error=error)
            _save(rows)


def _legacy_check_targets(accounts):
    # A registered legacy profile is already checked above; never open it twice.
    profiles = {str(Path(row['profile']).resolve()) for row in accounts if row.get('profile')}
    credentials = {str(Path(row['credential']).resolve()) for row in accounts if row.get('credential')}
    targets = []
    credential = Path('data/credentials/bilibili.json')
    if credential.is_file() and str(credential.resolve()) not in credentials:
        targets.append({'platform': 'bili', 'name': 'B站旧会话', 'credential': str(credential)})
    for platform in ('douyin', 'xhs', 'channels'):
        profile = Path('data/browser') / platform
        if profile.is_dir() and str(profile.resolve()) not in profiles:
            targets.append({'platform': platform, 'name': PLATFORMS[platform]['name'] + '旧会话', 'profile': str(profile)})
    return targets


def check_login_states(update):
    """Verify every configured account, then discover unused legacy sessions."""
    with _lock:
        accounts = _load()
    targets = [(row, 'existing') for row in accounts] + [(row, 'legacy') for row in _legacy_check_targets(accounts)]
    results = []
    update(f'准备检测 {len(targets)} 个账号 / 会话…', 0, len(targets))
    for index, (row, source) in enumerate(targets):
        platform = row['platform']
        update(f'正在检测 {index + 1}/{len(targets)}：{PLATFORMS[platform]["name"]} · {row["name"]}', index, len(targets))
        result = {'id': row.get('id'), 'account': row['name'], 'platform': platform,
                  'source': source, 'added': False, 'status': 'error'}
        try:
            identity, name = _verify_session(row)
            if source == 'legacy':
                with _lock:
                    existing = next((account for account in _load() if account['platform'] == platform
                                     and str(account['identity']) == str(identity)), None)
                if existing:
                    # Do not replace an existing account's independent session,
                    # or overwrite its failed verification with this old session.
                    result.update(account=name, status='connected', message='旧会话验证通过，该账号已在列表中；原连接保持不变。')
                else:
                    account = _register(platform, identity, name, profile=row.get('profile'), credential=row.get('credential'))
                    result.update(id=account['id'], account=name, added=True, status='connected', message='验证通过，已加入账号列表。')
            else:
                result.update(status='connected', message='登录有效，账号身份一致。')
        except LoginCheckFailure as exc:
            result.update(status=exc.status, message=str(exc))
        except Exception as exc:
            message = str(exc)
            if any(word in message.lower() for word in ('permission denied', 'operation not permitted', 'access denied')):
                message = '浏览器或会话访问被系统权限阻止，登录状态未确认。'
            elif isinstance(exc, BrowserError):
                message = '无法启动浏览器：' + (str(exc)[:400] or '请确认 Chrome 或 Edge 可用后重试。')
            elif 'timeout' in message.lower() or 'timed out' in message.lower():
                message = '访问平台超时，登录状态未确认，请检查网络后重试。'
            else:
                message = '检测失败：' + (message[:300] or type(exc).__name__)
            result.update(status='error', message=message)
        result['checked_at'] = time.time()
        if result['id']:
            _record_login_check(result['id'], result['status'], result['checked_at'],
                                None if result['status'] == 'connected' else result['message'])
        results.append(result)
        update(f'已检测 {index + 1}/{len(targets)} 个账号 / 会话', index + 1, len(targets))
    return results


def adopt_existing(update):
    # Retain the old API route while using the same complete verification.
    return check_login_states(update)


def update_account(account_id, fields, include_private):
    with _lock:
        rows = _load()
        row = next((x for x in rows if x['id'] == account_id), None)
        if not row:
            raise ValueError('账号不存在')
        allowed = {key for key, _ in FIELDS[row['platform']]}
        if not isinstance(fields, list) or not fields or any(key not in allowed for key in fields):
            raise ValueError('请至少选择一个可导出字段')
        if include_private and row['platform'] == 'bili':
            raise ValueError('B站公开作品接口不返回仅自己可见的视频，无法将其加入导出')
        row['fields'] = fields
        row['include_private'] = bool(include_private)
        _save(rows)
        return _public(row)


def _set_connection_status(account_id, status):
    with _lock:
        rows = _load()
        row = next((x for x in rows if x['id'] == account_id), None)
        if row:
            row['connection_status'] = status
            _save(rows)


def _remove_managed(path, root, is_directory):
    if not path:
        return
    candidate = Path(path).resolve()
    root = root.resolve()
    if candidate == root or not candidate.is_relative_to(root):
        return
    if is_directory:
        shutil.rmtree(candidate, ignore_errors=True)
    else:
        candidate.unlink(missing_ok=True)


def delete_account(account_id):
    """Forget an account and remove only credentials owned by this workbench."""
    with _lock:
        rows = _load()
        row = next((x for x in rows if x['id'] == account_id), None)
        if not row:
            raise ValueError('账号不存在')
        _save([x for x in rows if x['id'] != account_id])
    _remove_managed(row.get('profile'), APP_DIR / 'profiles', True)
    _remove_managed(row.get('credential'), APP_DIR / 'credentials', False)
    _remove_managed(APP_DIR / 'avatars' / f'{account_id}.png', APP_DIR / 'avatars', False)
    return _public(row)


def reconnect_account(account_id, update):
    """Refresh the saved session while verifying that it is the same account."""
    old = get_account(account_id)
    if not old:
        raise ValueError('账号不存在')
    platform = old['platform']
    staging = APP_DIR / 'pending' / uuid.uuid4().hex
    staging.mkdir(parents=True)
    cls = {'bili': BiliBrowser, 'douyin': DouyinBrowser,
           'xhs': XhsBrowser, 'channels': ChannelsBrowser}[platform]
    try:
        update('正在打开平台登录页面…')
        with cls(profile_dir=staging, headless=False) as browser:
            browser.open()
            if isinstance(getattr(browser, 'browser_message', None), str) and browser.browser_message:
                update(browser.browser_message)
            if platform == 'bili':
                browser.goto()
                if not browser.is_logged_in():
                    browser.open_login_dialog()
            elif platform == 'douyin':
                browser.goto()
            elif platform == 'xhs':
                browser.goto(LOGIN_URL)
            else:
                browser.open_login()
            if platform == 'channels' or not browser.is_logged_in():
                update('请扫码并在手机上选择原视频号“{}”；等待期间不会刷新二维码。'.format(old['name']) if platform == 'channels' else '请在打开的浏览器中重新登录原账号…')
                browser.wait_for_login(timeout=300)
            if platform == 'bili':
                cookie = browser.cookie_string()
                info = check_login(BiliClient(cookie=cookie, delay=(0, 0)))
                if not info.get('ok') or not info.get('mid'):
                    raise ValueError('无法确认 B站账号身份，连接未更改。')
                identity, name = str(info['mid']), info.get('name') or old['name']
                avatar = fetch_avatar(info.get('avatar'), browser)
            else:
                identity, name = _identity(browser, platform)
                name = _registration_name(browser, platform, name)
                avatar = browser_avatar(browser, platform)
        if str(identity) != str(old['identity']):
            raise ValueError('登录的是另一个账号，原连接未更改。请重新登录“{}”。'.format(old['name']))
        target = APP_DIR / 'profiles' / platform / uuid.uuid4().hex
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(target))
        credential = None
        if platform == 'bili':
            credential = APP_DIR / 'credentials' / f'{uuid.uuid4().hex}.json'
            save_credentials(cookie, mid=identity, name=name, path=credential)
        with _lock:
            rows = _load()
            stored = next((x for x in rows if x['id'] == account_id), None)
            if not stored:
                raise ValueError('账号已被删除，连接未保存。')
            previous_profile, previous_credential = stored.get('profile'), stored.get('credential')
            stored.update(name=name, profile=str(target), credential=str(credential) if credential else None,
                          connection_status='connected', last_checked_at=time.time(), last_check_error=None)
            _save(rows)
        _remove_managed(previous_profile, APP_DIR / 'profiles', True)
        _remove_managed(previous_credential, APP_DIR / 'credentials', False)
        _store_avatar(account_id, avatar)
        return _public(get_account(account_id))
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _collect(row, update):
    platform = row['platform']
    update(f'正在从{PLATFORMS[platform]["name"]}采集作品…')
    if platform == 'bili':
        # Public-space data alone cannot prove that the saved login still works.
        _verify_session(row)
        credentials = load_credentials(row['credential'])
        if not credentials or str(credentials.get('mid')) != row['identity']:
            raise ValueError('B站登录凭据缺失或与账号不符，请重新添加账号。')
        client = BiliClient(cookie=credentials['cookie'], delay=(0.2, 0.4))
        if not avatar_path(row['id']):
            info = check_login(client)
            if info.get('ok') and str(info.get('mid')) == row['identity']:
                _store_avatar(row['id'], fetch_avatar(info.get('avatar')))
        videos, _ = client.space_videos(int(row['identity']))
        if any(key in row['fields'] for key in ('like', 'like_rate')):
            update('正在补齐 B站作品的点赞数据…')
            def fetch_detail(video):
                try:
                    return BiliClient(cookie=credentials['cookie'], delay=(0, 0)).video_detail(video['bvid'])
                except BiliError:
                    return None
            with ThreadPoolExecutor(max_workers=4) as pool:
                for video, detail in zip(videos, pool.map(fetch_detail, videos)):
                    if detail:
                        video['like'] = detail['like']
                        if detail['view'] is not None:
                            video['play'] = detail['view']
        for video in videos:
            video['visibility'] = 'public'  # Public space API omits private uploads.
    elif platform == 'douyin':
        with DouyinBrowser(profile_dir=row['profile'], headless=True) as b:
            b.open(); b.goto()
            if not b.is_logged_in():
                raise ValueError('抖音登录已过期，请重新登录。')
            current, _ = _identity(b, platform)
            if current != row['identity']:
                raise ValueError('浏览器登录的抖音账号已变更，为避免导出错号已停止。')
            if not avatar_path(row['id']):
                _store_avatar(row['id'], browser_avatar(b, platform))
            files = b.export_videos()
        videos = []
        for path in files:
            videos.extend(parse_export_file(path))
    elif platform == 'xhs':
        with XhsBrowser(profile_dir=row['profile'], headless=True) as b:
            b.open(); b.goto(NOTE_MANAGE_URL, settle=0.5)
            for _ in range(12):
                if b.is_logged_in():
                    break
                b.page.wait_for_timeout(250)
            if not b.is_logged_in():
                raise ValueError('小红书登录已过期，请重新登录。')
            current, _ = _identity(b, platform)
            if current != row['identity']:
                raise ValueError('浏览器登录的小红书账号已变更，为避免导出错号已停止。')
            if not avatar_path(row['id']):
                _store_avatar(row['id'], browser_avatar(b, platform))
            videos, errors = XhsCollector(b).collect(max_pages=1000)
            if errors:
                raise ValueError('小红书采集不完整：' + '; '.join(errors[:2]))
    else:
        with ChannelsBrowser(profile_dir=row['profile'], headless=True) as b:
            b.open(); b.goto(PLATFORM_URL, settle=1)
            for _ in range(12):
                if b.is_logged_in():
                    break
                b.page.wait_for_timeout(250)
            if not b.is_logged_in():
                raise ValueError('视频号登录已过期，请重新登录。')
            current, _ = _identity(b, platform)
            if current != row['identity']:
                raise ValueError('浏览器登录的视频号账号已变更，为避免导出错号已停止。')
            if not avatar_path(row['id']):
                _store_avatar(row['id'], browser_avatar(b, platform))
            videos, errors = ChannelsCollector(b).collect(max_pages=1000)
            if errors:
                raise ValueError('视频号采集不完整：' + '; '.join(errors[:2]))
    if not videos:
        raise ValueError('没有采集到作品；未生成空表。')
    return videos


def refresh_missing_avatars(update):
    """Fill existing connections using their own verified platform sessions."""
    results = []
    for row in list_accounts():
        if avatar_path(row['id']):
            continue
        row = get_account(row['id'])
        platform = row['platform']
        update(f'正在获取{PLATFORMS[platform]["name"]}账号头像…')
        image = None
        try:
            if platform == 'bili':
                credential = load_credentials(row['credential'])
                info = check_login(BiliClient(cookie=credential['cookie'], delay=(0, 0))) if credential else {}
                if info.get('ok') and str(info.get('mid')) == row['identity']:
                    image = fetch_avatar(info.get('avatar'))
            else:
                cls = {'douyin': DouyinBrowser, 'xhs': XhsBrowser, 'channels': ChannelsBrowser}[platform]
                url = {'douyin': 'https://creator.douyin.com/creator-micro/home',
                       'xhs': NOTE_MANAGE_URL, 'channels': PLATFORM_URL}[platform]
                with cls(profile_dir=row['profile'], headless=True) as browser:
                    browser.open()
                    browser.goto(url, settle=3)
                    for _ in range(12):
                        if browser.is_logged_in():
                            break
                        browser.page.wait_for_timeout(250)
                    if browser.is_logged_in():
                        identity, _ = _identity(browser, platform)
                        if identity == row['identity']:
                            image = browser_avatar(browser, platform)
            _store_avatar(row['id'], image)
        except Exception:
            pass
        results.append({'platform': platform, 'account': row['name'], 'ok': bool(image)})
    return results


def _safe_filename(name):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', name).strip(' .')[:60] or '账号'


def _public_videos(platform, videos):
    if platform == 'douyin':
        return [video for video in videos if is_public(video)]
    if platform == 'bili':
        return videos  # Source is the public space list.
    unknown = sum(video.get('visibility') not in ('public', 'private') for video in videos)
    if unknown:
        raise ValueError(f'{PLATFORMS[platform]["name"]}有 {unknown} 条作品未返回可见性标记，无法保证剔除仅自己可见作品；未生成文件。')
    return [video for video in videos if video['visibility'] == 'public']


def _export_one(row, destination, update):
    started = time.monotonic()
    try:
        videos = _collect(row, update)
        available = [key for key, _ in FIELDS[row['platform']] if key in ('like_rate','url') or any(v.get(key) is not None for v in videos)]
        exported = videos if row['include_private'] else _public_videos(row['platform'], videos)
        if not exported:
            raise ValueError('筛选后没有可导出的作品')
        platform_name = PLATFORMS[row['platform']]['name']
        platform_dir = destination / platform_name
        platform_dir.mkdir(exist_ok=True)
        stamp = time.strftime('%Y%m%d%H%M')
        base = f'{_safe_filename(row["name"])}-{platform_name}视频数据-{stamp}'
        with _lock:
            path = platform_dir / f'{base}.xlsx'
            suffix = 2
            while path.exists():
                path = platform_dir / f'{base}-{suffix}.xlsx'
                suffix += 1
            write_selected_xlsx(exported, path, row['fields'], dict(FIELDS[row['platform']]), row['name'],
                                stats_videos=[v for v in exported if is_public(v)] if row['platform'] == 'douyin' else exported,
                                platform=row['platform'])
        with _lock:
            rows = _load()
            stored = next((x for x in rows if x['id'] == row['id']), None)
            if stored:
                stored['last_export'] = time.strftime('%Y-%m-%d %H:%M')
                stored['available_fields'] = available
                stored['connection_status'] = 'connected'
                stored['last_checked_at'] = time.time()
                stored['last_check_error'] = None
                _save(rows)
        return {'account': row['name'], 'platform': row['platform'], 'ok': True,
                'count': len(exported), 'path': str(path), 'seconds': round(time.monotonic()-started, 1)}
    except Exception as exc:
        message = str(exc)
        if isinstance(exc, LoginCheckFailure):
            _record_login_check(row['id'], exc.status, time.time(), message)
        elif any(word in message for word in ('登录已过期', '凭据缺失', '重新登录')):
            _record_login_check(row['id'], 'expired', time.time(), message)
        return {'account': row['name'], 'platform': row['platform'], 'ok': False,
                'error': message, 'seconds': round(time.monotonic()-started, 1)}


def export_accounts(account_ids=None):
    """Resolve an explicit selection; an empty/invalid selection never exports all."""
    with _lock:
        accounts = _load()
    if account_ids is None:
        return accounts
    if not isinstance(account_ids, list) or any(not isinstance(value, str) for value in account_ids):
        raise ValueError('无效的导出账号选择')
    if not account_ids:
        raise ValueError('请至少勾选一个账号')
    ids = set(account_ids)
    if ids - {row['id'] for row in accounts}:
        raise ValueError('所选账号不存在，请刷新账号列表后重新选择')
    return [row for row in accounts if row['id'] in ids]


def export_all(folder, update, account_ids=None):
    destination = Path(folder).expanduser().resolve()
    if not destination.is_dir():
        raise ValueError('请选择已经存在的导出文件夹')
    accounts = export_accounts(account_ids)
    if not accounts:
        raise ValueError('请先添加账号')
    results = [None] * len(accounts)
    update(f'正在采集 0/{len(accounts)} 个账号…', 0, len(accounts))
    with ThreadPoolExecutor(max_workers=min(4, len(accounts))) as pool:
        futures = {pool.submit(_export_one, row, destination, update): index
                   for index, row in enumerate(accounts)}
        for future in as_completed(futures):
            index = futures[future]
            results[index] = future.result()
            completed = sum(value is not None for value in results)
            update(f'{completed}/{len(accounts)} 个账号已处理', completed, len(accounts))
    return results
