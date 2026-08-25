@echo off
setlocal
chcp 65001 >nul
title OBS 权限助手 - 启动器
cd /d "%~dp0"

echo ================================================
echo   OBS 权限助手  启动器
echo ================================================

rem ---- 1. 找一个可用的 Python ----
set "PYCMD="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"
if not defined PYCMD (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD (
    echo [错误] 没有找到 Python。
    echo 解决方法：安装 Python 3.10+ 并勾选 "Add Python to PATH"；
    echo           或在本目录命令行手动运行：
    echo               py -3 -m pip install -r requirements.txt
    echo               py -3 app\gui.py
    echo.
    echo 按任意键退出...
    pause >nul
    exit /b 1
)
echo [1/3] 使用 Python: %PYCMD%
%PYCMD% -c "import sys; print('       版本:', sys.version.split()[0])"

rem ---- 2. 检查 / 安装 mitmproxy ----
echo [2/3] 检查 mitmproxy ...
%PYCMD% -c "import mitmproxy" >nul 2>nul
if errorlevel 1 (
    echo        未安装，正在安装依赖（mitmproxy）...
    %PYCMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试。
        echo 按任意键退出...
        pause >nul
        exit /b 1
    )
) else (
    echo        已安装
)

rem ---- 3. 校验程序模块 ----
cd /d "%~dp0app"
echo [3/3] 校验程序模块 ...
%PYCMD% -c "import gui" >nul 2>nul
if errorlevel 1 (
    echo [错误] 程序模块加载失败，详细信息如下：
    %PYCMD% -c "import gui"
    echo 按任意键退出...
    pause >nul
    exit /b 1
) else (
    echo        模块 OK
)

rem ---- 4. 无窗口启动界面 ----
echo 正在启动 OBS 权限助手 ...
%PYCMD% "_launch.py"
exit /b 0
