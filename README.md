# OVideoStats

多平台创作数据采集与 Excel 导出工具，支持 B站、抖音、小红书和视频号。

Connect your accounts, collect your content metrics, and export Excel files on your own computer.

## 功能

- 多平台、多账号独立登录，检测登录状态并重新连接。
- 选择导出账号和 Excel 字段，按平台分类，每个账号生成一份 `.xlsx`。
- 采集各平台提供的播放、点赞、评论、收藏等指标；缺失字段留空。
- 默认仅导出可确认的公开作品；可按账号设置是否包含非公开作品。
- 记住导出文件夹、账号排序、奶油白/暖夜黑/跟随系统外观。

## 安装与使用

公开版：**0.1.4**。

[下载 v0.1.4 安装包](https://github.com/OAKunO/OVideoStats/releases/tag/v0.1.4) · [SHA-256 校验清单](https://github.com/OAKunO/OVideoStats/releases/download/v0.1.4/SHA256SUMS.txt)

- macOS：当前提供 Apple silicon 版本，使用系统 WebKit 原生窗口。解压 ZIP 后把 `OVideoStats.app` 放入“应用程序”。本次构建使用 macOS 26 环境，尚未验证更早系统；这是本地临时签名版本，未做 Apple 公证。
- Windows：Windows 10 / 11 x64，运行 `OVideoStats-Setup-0.1.4-x64.exe`。包含 Python 和依赖，无需安装开发环境。缺少 WebView2 时安装器会联网补装微软官方运行库。当前安装包未签名，Windows 实机安装与运行尚待验证。
- 登录和采集需要已安装 Chrome 或 Edge。新连接优先使用系统默认的兼容浏览器；已有账号保持各自的浏览器归属。

启动后选择“新增账号”，按平台要求完成扫码或短信登录。勾选要导出的账号，选择本机文件夹，然后点击“采集并导出”。

## 数据与隐私

安装包不含任何预置账号、Cookie、登录态或采集历史。账号和数据保存在使用者的电脑中；Excel 导出到自行选择的文件夹。软件没有开发者网站上传、遥测或服务器同步功能。

登录/采集需要访问相应平台，不能离线采集。完整说明见 [PRIVACY.md](PRIVACY.md)。

## 从源码运行

需要 Python 3.9+、Node.js 20+、npm，以及 Chrome/Edge。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-desktop.txt
cd ui
npm ci
npm run build
cd ..
.venv/bin/python desktop_app.py
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
cd ui
npm ci
npm run build
cd ..
.venv\Scripts\python.exe desktop_app.py
```

macOS 的 `start.command` 启动本机 Web 开发模式，`desktop_app.py` 启动原生窗口。修改前端后请重新构建。

## 测试

```sh
.venv/bin/python -m unittest discover -s tests
node --test ui/src/*.test.js
```

测试采用模拟平台响应和合成账号资料，不需要真实账号或平台网络请求。

## 构建安装包

macOS 原生构建：

```sh
.venv/bin/python packaging/build_macos.py
```

Windows 原生 PyInstaller 构建：

```powershell
.venv\Scripts\python.exe packaging\build_windows.py
```

使用 Pynsist/NSIS 构建 Windows 安装器的步骤见 [WINDOWS.md](WINDOWS.md)。公开源码导出工具：

```sh
python3 packaging/prepare_public_release.py --destination /tmp/OVideoStats-source
```

导出使用文件白名单，不复制 `data/`、`outputs/`、安装包、虚拟环境、Git 历史或个人网站项目。将导出目录作为新的仓库起点，不要上传使用中的工作目录。

## 目录

- `desktop_app.py`：原生窗口、本机服务与用户数据目录。
- `studio_app.py`：仅监听本机的 API 和静态界面。
- `smi/`：平台适配、账号管理和 Excel 导出。
- `ui/`：React/Vite 界面与静态图标、字体。
- `packaging/`：构建脚本和应用图标。
- `tests/`：不使用真实账号的测试。
- `LICENSES/`：第三方组件许可原文与构建依赖清单。

## 贡献与许可

欢迎改进平台适配和跨平台体验，见 [CONTRIBUTING.md](CONTRIBUTING.md)。

原创代码使用 [MIT License](LICENSE)。第三方组件、字体、平台图标各自保留原许可与权利归属，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

本项目与各平台无隶属或合作关系。平台页面和接口可能变化，数据字段与访问范围以平台实际返回为准。
