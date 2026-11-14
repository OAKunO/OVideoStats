"""Build a real Windows x64 NSIS installer on macOS, Linux or Windows.

Uses Windows wheels and the official embedded Windows Python, never host binaries.
Run with the isolated builder-env Python after preparing the pinned wheel directory.
"""
import argparse
from email.parser import Parser
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile

from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.utils import canonicalize_name
import nsist
import nsist.wheels

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/windows-installer'
PYTHON_VERSION = '3.13.17'
APP_VERSION = '0.1.4'


class ModernWheelScorer(nsist.wheels.CompatibilityScorer):
    def __init__(self, py_version, platform):
        super().__init__(py_version, platform)
        # pythonnet uses a valid CPython-specific, ABI-independent pure wheel.
        interpreter = 'cp' + ''.join(py_version.split('.')[:2])
        self.tag_prio[(interpreter, 'none', 'any')] = 1


def validate_wheels(directory):
    environment = default_environment()
    environment.update(sys_platform='win32', platform_system='Windows',
                       platform_machine='AMD64', python_version='3.13',
                       python_full_version=PYTHON_VERSION, extra='')
    scorer = ModernWheelScorer(PYTHON_VERSION, 'win_amd64')
    packages = {}
    for wheel in sorted(directory.glob('*.whl')):
        if not scorer.is_compatible(wheel.name):
            raise ValueError('Not a compatible Windows wheel: ' + wheel.name)
        with ZipFile(wheel) as archive:
            assert archive.testzip() is None, wheel
            metadata = next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))
            metadata = Parser().parsestr(archive.read(metadata).decode())
        name = canonicalize_name(metadata['Name'])
        if name in packages:
            raise ValueError('Duplicate wheel: ' + name)
        requires_python = metadata.get('Requires-Python')
        if requires_python and not SpecifierSet(requires_python).contains(PYTHON_VERSION):
            raise ValueError('Python version mismatch: ' + wheel.name)
        packages[name] = {'version': metadata['Version'], 'requires': metadata.get_all('Requires-Dist') or [],
                          'wheel': wheel.name, 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()}
    for required in ['pywebview', 'pythonnet', 'playwright', 'pillow', 'openpyxl']:
        if required not in packages:
            raise ValueError('Missing application dependency: ' + required)
    for name, package in packages.items():
        for requirement in package['requires']:
            req = Requirement(requirement)
            if req.marker is not None and not req.marker.evaluate(environment):
                continue
            dependency = packages.get(canonicalize_name(req.name))
            if dependency is None or not req.specifier.contains(dependency['version'], prereleases=True):
                raise ValueError('{} needs {}'.format(name, req))
    return packages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--skip-ui-build', action='store_true')
    args = parser.parse_args()
    # Keep the installer, shortcuts and in-app icon on the current source asset.
    from build_windows import make_icon
    make_icon()
    wheels = WORK / 'wheels-cp313'
    packages = validate_wheels(wheels)
    if not args.skip_ui_build:
        subprocess.run(['npm.cmd' if sys.platform == 'win32' else 'npm', 'run', 'build'], cwd=ROOT / 'ui', check=True)
    bootstrapper = WORK / 'MicrosoftEdgeWebview2Setup.exe'
    if not bootstrapper.is_file() or bootstrapper.read_bytes()[:2] != b'MZ':
        raise ValueError('Download the official Microsoft WebView2 bootstrapper first.')
    stage = WORK / 'source'
    payload = stage / 'pynsist_pkgs'
    if stage.exists():
        shutil.rmtree(stage)
    payload.mkdir(parents=True)
    for filename in ['desktop_app.py', 'studio_app.py']:
        shutil.copy2(ROOT / filename, payload / filename)
    for dirname in ['smi', 'dist', 'LICENSES']:
        shutil.copytree(ROOT / dirname, payload / dirname,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    shutil.copy2(ROOT / 'packaging/windows_entry.py', payload / 'windows_entry.py')
    for filename in ['LICENSE', 'PRIVACY.md', 'THIRD_PARTY_NOTICES.md']:
        shutil.copy2(ROOT / filename, payload / filename)
    shutil.copy2(ROOT / 'packaging/OVideoStats.ico', payload / 'app-icon.ico')
    shutil.copy2(ROOT / 'packaging/windows_installer.nsi', stage / 'windows_installer.nsi')
    (payload / 'WINDOWS-README.txt').write_text(
        'OVideoStats Windows 10/11 x64\n\n已包含 Python 与运行依赖，无需构建。\n'
        '新连接优先使用系统默认的 Chrome 或 Edge；其他默认浏览器会自动改用已安装的兼容浏览器。\n'
        '已有账号保留原登录浏览器；需要更换时请重新连接同一账号。\n'
        '用户数据保存在 %LOCALAPPDATA%\\OVideoStats，与安装目录分离。\n'
        '本包不包含账号、Cookie 或浏览器登录态。\n', encoding='utf-8')
    os.environ['PYNSIST_CACHE_DIR'] = str(WORK / 'cache')
    nsist.wheels.CompatibilityScorer = ModernWheelScorer
    os.chdir(stage)
    name = 'OVideoStats-Setup-{}-x64.exe'.format(APP_VERSION)
    builder = nsist.InstallerBuilder(
        appname='OVideoStats', version=APP_VERSION, publisher='OAKunO',
        shortcuts={'OVideoStats': {'entry_point': 'windows_entry:main',
                                  'console': False, 'icon': str(ROOT / 'packaging/OVideoStats.ico')}},
        icon=str(ROOT / 'packaging/OVideoStats.ico'), py_version=PYTHON_VERSION,
        py_bitness=64, build_dir=str(WORK / 'nsis'), installer_name=name,
        nsi_template='windows_installer.nsi', local_wheels=[str(wheels / '*.whl')],
        extra_files=[(str(bootstrapper), '$INSTDIR')],
    )
    builder.run(makensis=False)
    manifest = {'python': PYTHON_VERSION, 'platform': 'win_amd64', 'packages': packages,
                'runtime_tested_on_windows': False, 'installer': name}
    (WORK / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    if args.prepare_only:
        print('Prepared:', builder.build_dir)
        return
    result = builder.run_nsis()
    if result:
        raise SystemExit(result)
    release = ROOT / 'release/windows'
    release.mkdir(parents=True, exist_ok=True)
    destination = release / name
    shutil.copy2(Path(builder.build_dir) / name, destination)
    print('Created:', destination)
    print('SHA256:', hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    main()
