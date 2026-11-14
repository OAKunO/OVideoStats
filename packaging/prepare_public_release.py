"""Export an allowlisted source tree without history, local state or build output."""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    '.gitignore', 'LICENSE', 'README.md', 'WINDOWS.md', 'PRIVACY.md',
    'CONTRIBUTING.md', 'THIRD_PARTY_NOTICES.md', 'desktop_app.py', 'studio_app.py',
    'requirements-studio.txt', 'requirements-desktop.txt',
    'setup-windows.bat', 'build-windows.bat', 'start.bat', 'start.command',
    'ui/index.html', 'ui/package.json', 'ui/package-lock.json', 'ui/vite.config.js',
    'packaging/build_macos.py', 'packaging/build_windows.py',
    'packaging/build_windows_installer.py', 'packaging/prepare_windows_installer.py',
    'packaging/prepare_public_release.py', 'packaging/windows_entry.py',
    'packaging/windows_installer.nsi', 'packaging/windows-runtime-lock.txt',
    'packaging/app-icon.png',
]


def export_source(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Destination already exists; choose a new empty path.')
    selected = [ROOT / name for name in FILES]
    for dirname, pattern in [('smi', '*.py'), ('tests', '*.py'),
                              ('ui/src', '*'), ('ui/public', '*'), ('LICENSES', '*')]:
        selected.extend(path for path in (ROOT / dirname).rglob(pattern)
                        if path.is_file() and '__pycache__' not in path.parts)
    missing = [str(path.relative_to(ROOT)) for path in selected if not path.is_file()]
    if missing:
        raise ValueError('Required release files missing: ' + ', '.join(missing))
    for path in selected:
        if path.is_symlink():
            raise ValueError('Source export does not accept symlinks: ' + str(path.relative_to(ROOT)))
        target = destination / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return len(selected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True)
    args = parser.parse_args()
    print('Exported {} public source files.'.format(export_source(args.destination)))
