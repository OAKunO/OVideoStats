"""Build a self-contained macOS app; run with the project's virtualenv Python."""

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'packaging'
ICON_SOURCE = PACKAGE / 'app-icon.png'
APP_VERSION = '0.1.4'


def make_icon():
    with Image.open(ICON_SOURCE) as source:
        image = source.convert('RGBA').resize((1024, 1024), Image.Resampling.LANCZOS)
    icon = PACKAGE / 'OVideoStats.icns'
    image.save(icon, format='ICNS')
    return icon


def main():
    if sys.platform != 'darwin':
        raise SystemExit('这个脚本只能在 macOS 上运行。')
    subprocess.run(['npm', 'run', 'build'], cwd=ROOT / 'ui', check=True)
    PACKAGE.mkdir(exist_ok=True)
    icon = make_icon()
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onedir', '--windowed', '--name', 'OVideoStats',
               '--osx-bundle-identifier', 'com.oakuno.ovideostats',
               '--icon', str(icon),
               '--add-data', '{}:dist'.format(ROOT / 'dist'),
               '--add-data', '{}:LICENSES'.format(ROOT / 'LICENSES'),
               '--collect-all', 'playwright',
               '--collect-all', 'webview',
               '--distpath', str(ROOT / 'release'),
               '--workpath', str(ROOT / 'build' / 'pyinstaller'),
               '--specpath', str(PACKAGE),
               str(ROOT / 'desktop_app.py')]
    for name in ['LICENSE', 'PRIVACY.md', 'THIRD_PARTY_NOTICES.md']:
        command[command.index('--collect-all'):command.index('--collect-all')] = [
            '--add-data', '{}:.'.format(ROOT / name)]
    environment = os.environ.copy()
    environment['PYINSTALLER_CONFIG_DIR'] = str(ROOT / 'build' / 'pyinstaller-config')
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    app = ROOT / 'release' / 'OVideoStats.app'
    # Wheel installation records describe the build environment, not the frozen app.
    # Keep runtime metadata and licenses, but exclude these source-path records.
    for record in app.rglob('*.dist-info/RECORD'):
        record.unlink()
    info_path = app / 'Contents' / 'Info.plist'
    info = plistlib.loads(info_path.read_bytes())
    info.update(CFBundleShortVersionString=APP_VERSION, CFBundleVersion=APP_VERSION)
    info_path.write_bytes(plistlib.dumps(info))
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
    # PyInstaller also emits a second onedir copy beside the standalone app.
    shutil.rmtree(ROOT / 'release' / 'OVideoStats', ignore_errors=True)
    archive = ROOT / 'release' / 'OVideoStats-macOS.zip'
    archive.unlink(missing_ok=True)
    subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent',
                    str(ROOT / 'release' / 'OVideoStats.app'), str(archive)], check=True)
    print('已生成:', ROOT / 'release' / 'OVideoStats.app')
    print('已生成:', archive)


if __name__ == '__main__':
    main()
