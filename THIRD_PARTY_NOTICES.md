# Third-party notices

OVideoStats original source code is licensed under MIT (see `LICENSE`). That license does not replace the terms of third-party software or the rights in platform logos.

## Included notices

`LICENSES/` contains license texts copied from the installed distributions and the pinned Windows wheels used for this release. `LICENSES/DEPENDENCIES.json` records package names, versions, target environments and notice paths. The macOS inventory also identifies build-environment tools; it does not imply that every build tool is shipped inside the app.

Runtime components include CPython, pywebview, Playwright and its Node.js driver, openpyxl, et_xmlfile, Pillow, bottle, proxy_tools, typing_extensions, and platform-specific dependencies. macOS uses PyObjC and the system WebKit. Windows includes pythonnet, clr-loader, cffi and their dependencies. Native operating-system components retain their own terms.

The frontend uses React, React DOM, scheduler, loose-envify and js-tokens. Development build tooling is described by `ui/package-lock.json` and installed from npm for source builds. The Noto Sans SC font is provided under SIL OFL; its original notice is retained in `ui/public/fonts/` and `LICENSES/NotoSansSC/`.

PyInstaller's GPL exception permits distributing generated applications under the application's own license, subject to dependency licenses. Its original exception text is retained with the collected notices. NSIS and Pynsist are build tools. Microsoft's WebView2 Evergreen Bootstrapper, Python's Windows runtime DLLs and Windows native redistributables retain their own provider terms; WebView2 may download and install Microsoft's runtime when missing. Distribution reference: https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution

## Platform icons and trademarks

Bilibili, Douyin, Xiaohongshu and WeChat Channels names and icons identify supported platforms. They are not licensed under OVideoStats' MIT license. Original icon source URLs are recorded in `ui/public/icons/SOURCES.md`. The application does not imply affiliation, sponsorship or endorsement by these platforms.

## Interface references

The platform adapters contain references to `orangexie05/creator-platform-data` and `3441293738/creatorhub` for interface paths, field names or login-page observations. These repositories are not bundled as dependencies. The public source export includes OVideoStats adapters and does not import either repository's source tree. Referenced repository links remain in the relevant adapter comments.

When updating dependencies, preserve their original notices and refresh the dependency inventory and bundled license texts before redistributing builds.
