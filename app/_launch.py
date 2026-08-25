"""启动器辅助：无窗口地启动 gui.py。

用法：python _launch.py
由 run.bat 调用，会在后台用 pythonw 启动图形界面并立即返回。
"""
import os
import subprocess
import sys

app_dir = os.path.dirname(os.path.abspath(__file__))
gui_py = os.path.join(app_dir, "gui.py")

# 优先使用同目录的 pythonw.exe（无控制台版本），找不到则退回当前解释器
w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
exe = w if os.path.exists(w) else sys.executable

subprocess.Popen([exe, gui_py], cwd=app_dir)
