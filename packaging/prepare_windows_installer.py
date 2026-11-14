"""Download pinned Windows wheels and Microsoft's WebView2 bootstrapper."""
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/windows-installer'
WEBVIEW2_BOOTSTRAPPER = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'


def main():
    wheels = WORK / 'wheels-cp313'
    wheels.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, '-m', 'pip', 'download', '--only-binary=:all:', '--no-deps',
        '--platform', 'win_amd64', '--implementation', 'cp', '--python-version', '3.13',
        '--abi', 'cp313', '-r', str(ROOT / 'packaging/windows-runtime-lock.txt'),
        '--dest', str(wheels),
    ], check=True)
    destination = WORK / 'MicrosoftEdgeWebview2Setup.exe'
    with urlopen(WEBVIEW2_BOOTSTRAPPER, timeout=60) as response:
        data = response.read()
    if data[:2] != b'MZ':
        raise ValueError('Microsoft bootstrapper is not a Windows executable.')
    destination.write_bytes(data)
    print('Prepared official WebView2 bootstrapper and pinned Windows wheels.')


if __name__ == '__main__':
    main()
