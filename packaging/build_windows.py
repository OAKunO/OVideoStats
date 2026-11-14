"""Build on Windows x64. No user data, credentials or browser profiles are bundled."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def make_icon():
    from PIL import Image
    icon = ROOT / 'packaging' / 'OVideoStats.ico'
    with Image.open(ROOT / 'packaging' / 'app-icon.png') as source:
        image = source.convert('RGBA')
    if image.getchannel('A').getextrema()[0] == 255:
        raise ValueError('应用图标缺少透明背景，请先检查 app-icon.png。')
    sizes = [(16,16), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)]
    frames = []
    for size in sizes:
        frame = image.resize(size, Image.Resampling.LANCZOS)
        # Lanczos can leave near-zero alpha ringing at small icon corners.
        frame.putalpha(frame.getchannel('A').point(lambda alpha: 0 if alpha < 4 else alpha))
        frames.append(frame)
    frames[-1].save(icon, format='ICO', sizes=sizes, append_images=frames[:-1])
    return icon


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-ui-build', action='store_true', help='Use the included, prebuilt shared UI')
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('Windows EXE 必须在 Windows 上构建，不能在 macOS 交叉打包。')
    if not args.skip_ui_build:
        subprocess.run(['npm.cmd', 'ci'], cwd=ROOT/'ui', check=True)
        subprocess.run(['npm.cmd', 'run', 'build'], cwd=ROOT/'ui', check=True)
    if not (ROOT/'dist'/'index.html').is_file():
        raise SystemExit('缺少 dist/index.html，请先构建界面。')
    icon = make_icon()
    release = ROOT/'release'/'windows'
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--onedir', '--windowed', '--name', 'OVideoStats', '--icon', str(icon),
        '--add-data', str(ROOT/'dist') + os.pathsep + 'dist',
        '--add-data', str(ROOT/'LICENSES') + os.pathsep + 'LICENSES',
        '--collect-all', 'playwright', '--collect-all', 'webview',
        '--hidden-import', 'webview.platforms.edgechromium',
        '--distpath', str(release), '--workpath', str(ROOT/'build'/'windows'),
        '--specpath', str(ROOT/'build'/'windows'), str(ROOT/'desktop_app.py')]
    for name in ['LICENSE', 'PRIVACY.md', 'THIRD_PARTY_NOTICES.md']:
        command[command.index('--collect-all'):command.index('--collect-all')] = [
            '--add-data', str(ROOT/name) + os.pathsep + '.']
    subprocess.run(command, cwd=ROOT, check=True)
    shutil.copy2(ROOT/'WINDOWS.md', release/'OVideoStats'/'README-Windows.md')
    archive = shutil.make_archive(str(release/'OVideoStats-Windows-x64'), 'zip', release, 'OVideoStats')
    print('已构建（请按 WINDOWS.md 验收）：', archive)


if __name__ == '__main__':
    main()
