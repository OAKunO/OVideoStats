"""Read the authenticated account avatar and normalize it for local storage."""
from io import BytesIO
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MAX_IMAGE_BYTES = 5 * 1024 * 1024
SELECTORS = {
    'douyin': ['button[class*="account-trigger"] img', '[class*="avatar-"] img'],
    'xhs': ['.d-topbar-default .user-info img:not(.arrow)'],
    'channels': ['[class*="finder-avatar"] img', '[class*="account"] [class*="avatar"] img',
                 '[class*="user"] [class*="avatar"] img', 'img[class*="avatar"]',
                 '[class*="avatar"] img'],
}


def normalized_png(raw):
    from PIL import Image, ImageOps
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('头像文件过大或为空')
    with Image.open(BytesIO(raw)) as image:
        if image.width * image.height > 16_000_000:
            raise ValueError('头像尺寸过大')
        image = ImageOps.exif_transpose(image).convert('RGBA')
        image = ImageOps.fit(image, (128, 128), method=Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format='PNG')
        return output.getvalue()


def fetch_avatar(url, browser=None):
    """Avatar failure is optional and must never fail a connection or export."""
    try:
        if not isinstance(url, str):
            return None
        if url.startswith('//'):
            url = 'https:' + url
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname:
            return None
        if browser:
            response = browser._ctx.request.get(url, headers={'Referer': browser.page.url}, timeout=5000)
            if not response.ok or int(response.headers.get('content-length', '0')) > MAX_IMAGE_BYTES:
                return None
            raw = response.body()
        else:
            request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'})
            with urlopen(request, timeout=5) as response:
                raw = response.read(MAX_IMAGE_BYTES + 1)
        return normalized_png(raw)
    except Exception:
        return None


def browser_avatar(browser, platform):
    try:
        url = browser.page.evaluate('''(selectors) => {
          for (const selector of selectors) {
            for (const img of document.querySelectorAll(selector)) {
              const rect = img.getBoundingClientRect();
              const src = img.currentSrc || img.src;
              if (rect.width >= 24 && rect.height >= 24 && src.startsWith('https:')) return src;
            }
          }
          return null;
        }''', SELECTORS[platform])
        return fetch_avatar(url, browser)
    except Exception:
        return None
