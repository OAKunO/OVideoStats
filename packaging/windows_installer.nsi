[% extends "pyapp_msvcrt.nsi" %]

[% block modernui %]
!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!define MUI_ABORTWARNING
!define MUI_ICON "[[icon]]"
!define MUI_UNICON "[[icon]]"
!define MUI_WELCOMEPAGE_TEXT "安装 OVideoStats。已包含 Python 运行时与采集依赖，无需自行构建。$\r$\n$\r$\n如缺少 WebView2，安装程序将联网补装微软官方运行库。登录优先使用默认的 Chrome 或 Edge；默认浏览器不兼容时自动选用已安装的兼容浏览器。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\Python\pythonw.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS '$\"$INSTDIR\OVideoStats.launch.pyw$\"'
!define MUI_FINISHPAGE_RUN_TEXT "运行 OVideoStats"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MULTIUSER_PAGE_INSTALLMODE
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"
VIProductVersion "[[ib.version]].0"
VIAddVersionKey /LANG=1033 "ProductName" "OVideoStats Setup"
VIAddVersionKey /LANG=1033 "FileDescription" "OVideoStats Windows Installer"
VIAddVersionKey /LANG=1033 "CompanyName" "OAKunO"
VIAddVersionKey /LANG=1033 "FileVersion" "[[ib.version]]"
VIAddVersionKey /LANG=1033 "LegalCopyright" "OAKunO"
[% endblock modernui %]

[% block sections %]
Section -WebView2
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "此安装包需要 64 位 Windows。"
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "OVideoStats 需要 Windows 10 或 Windows 11。"
    Abort
  ${EndIf}
  SetRegView 32
  ReadRegStr $0 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${If} $0 == ""
  ${OrIf} $0 == "0.0.0.0"
    ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${EndIf}
  ${If} $0 == ""
  ${OrIf} $0 == "0.0.0.0"
    DetailPrint "正在安装 Microsoft Edge WebView2 Runtime..."
    InitPluginsDir
    File /oname=$PLUGINSDIR\MicrosoftEdgeWebview2Setup.exe "MicrosoftEdgeWebview2Setup.exe"
    ExecWait '"$PLUGINSDIR\MicrosoftEdgeWebview2Setup.exe" /silent /install' $1
    ReadRegStr $0 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
    ${If} $0 == ""
    ${OrIf} $0 == "0.0.0.0"
      ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
    ${EndIf}
    ${If} $0 == ""
    ${OrIf} $0 == "0.0.0.0"
      MessageBox MB_ICONSTOP "WebView2 安装未完成。请检查网络后重试。微软安装程序返回代码：$1"
      Abort
    ${EndIf}
  ${EndIf}
  SetRegView 64
SectionEnd
[[ super() ]]
[% endblock sections %]

[% block install_shortcuts %]
  SetOutPath "$INSTDIR"
  CreateShortCut "$SMPROGRAMS\OVideoStats.lnk" "$INSTDIR\Python\pythonw.exe" '"$INSTDIR\OVideoStats.launch.pyw"' "$INSTDIR\OVideoStats.ico"
  CreateShortCut "$DESKTOP\OVideoStats.lnk" "$INSTDIR\Python\pythonw.exe" '"$INSTDIR\OVideoStats.launch.pyw"' "$INSTDIR\OVideoStats.ico"
[% endblock install_shortcuts %]

[% block uninstall_shortcuts %]
  [[ super() ]]
  Delete "$DESKTOP\OVideoStats.lnk"
[% endblock uninstall_shortcuts %]
