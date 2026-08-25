@echo off
setlocal
chcp 65001 >nul
title OBS 权限助手 (打包 exe)
cd /d "%~dp0"

rem ---- 定位 Python（优先 py -3，其次 python）----
set "PYCMD="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"
if not defined PYCMD (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD (
    echo [错误] 没有找到 Python，无法打包。
    echo 请安装 Python 3.10+ 并勾选 "Add Python to PATH"。
    echo 按任意键退出...
    pause >nul
    exit /b 1
)
echo 使用 Python: %PYCMD%

echo [1/3] 安装依赖...
%PYCMD% -m pip install -r requirements.txt pyinstaller || goto :err

echo [2/3] 用 PyInstaller 打包...
%PYCMD% -m PyInstaller --noconfirm --onefile --windowed --name OBSHelper ^
  --collect-all mitmproxy ^
  --collect-all mitmproxy_rs ^
  --hidden-import h2 ^
  --hidden-import hyperframe ^
  --hidden-import hpack ^
  --hidden-import wsproto ^
  app/gui.py || goto :err

echo [3/3] 完成！可执行文件在 dist\OBSHelper.exe
echo 按任意键退出...
pause >nul
exit /b 0

:err
echo 打包失败，请检查上方错误信息。
echo 按任意键退出...
pause >nul
exit /b 1
