# OVideoStats · Windows

## 使用安装包

支持 Windows 10 / 11 x64。安装器为 `OVideoStats-Setup-0.1.4-x64.exe`，包含官方 Windows x64 CPython 3.13.17 和固定版本的运行依赖。

安装时检查 Microsoft Edge WebView2，缺少时使用微软官方 Evergreen Bootstrapper 联网安装。登录和采集另外需要 Chrome/Edge。软件数据位于 `%LOCALAPPDATA%\OVideoStats\data`，与安装目录分离。首次使用需连接自己的账号。

当前安装包未签名。已做静态打包验证，尚未在 Windows 实机完成安装、WebView2、登录和导出验收。

## 原生 PyInstaller 构建

安装 Python 3.11+、Node.js 20+ 和 npm，运行 `setup-windows.bat` 后在 `ui/` 运行 `npm ci`、`npm run build`，再运行 `build-windows.bat`。

也可直接执行：

```powershell
.venv\Scripts\python.exe packaging\build_windows.py
```

生成便携 ZIP。必须完整解压后运行 `OVideoStats.exe`，不能只拷贝单个 EXE。

## Pynsist / NSIS 安装器

这条路线可以在 macOS、Linux 或 Windows 上封装官方 Windows 运行时，不会把构建机的 Python 二进制放入 Windows 包。

1. 在构建环境安装本项目 Python 依赖、`pynsist==2.8` 和 NSIS（确保 `makensis` 可运行）。
2. 在 `ui/` 运行 `npm ci`、`npm run build`。
3. 运行 `python packaging/prepare_windows_installer.py`，下载固定版本的 Windows wheel 和微软官方 WebView2 引导程序。
4. 运行 `python packaging/build_windows_installer.py --skip-ui-build`。

版本及架构：`packaging/windows-runtime-lock.txt`、`packaging/build_windows_installer.py`。构建清单写入 `build/windows-installer/manifest.json`，安装器写入 `release/windows/`。

安装包包括应用许可、隐私说明和 `LICENSES/`。不包含用户数据、浏览器资料、开发路径或源码迁移提示。

## 实机验证范围

正式使用前请在 Windows 上检查安装、首次启动、WebView2 缺失、中文路径、系统缩放、账号连接和 Excel 导出。macOS 的静态检查不能证明这些 Windows 运行流程已经通过。
