# -*- coding: utf-8 -*-
"""
gui.py
======
OBS权限助手 - 抖音直播伴侣推流码获取器（图形界面主程序）

启动方式：  python gui.py
打包方式：  见 build.bat（PyInstaller）

流程：
  1) 启动代理（进程内 mitmproxy，监听 127.0.0.1:<端口>）
  2) 安装 mitmproxy CA 证书到「受信任的根证书颁发机构」
  3) 设置系统代理指向本代理
  4) 打开抖音直播伴侣，点击「开始直播」
  5) 软件自动拦截并解析出 RTMP 推流服务器地址 + 串流密钥
  6) （可选）自动结束直播伴侣进程，交给 OBS 推流
"""

import asyncio
import json
import os
import queue
import socket
import sys
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter import scrolledtext

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.expanduser("~"), ".obs_helper")
RESULT_FILE = os.path.join(DATA_DIR, "captured.json")
SEEN_FLOW_FILE = os.path.join(DATA_DIR, "seen_flows.log")
DEBUG_DUMP_DIR = os.path.join(DATA_DIR, "debug")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import proxy_utils  # noqa: E402
from capturer import CaptureAddon  # noqa: E402


# ---------------------------------------------------------------------------
# 项目信息 / 开源声明
# ---------------------------------------------------------------------------
PROJECT_NAME = "OBS权限助手"
PROJECT_URL = "https://github.com/PCYBlurF/OBS-Helper"


def show_open_source_notice(parent):
    """启动时展示开源声明弹窗；确认后才进入主界面。"""
    top = tk.Toplevel(parent)
    top.title("开源声明")
    top.configure(bg="white")
    top.resizable(False, False)
    # 注意：不能使用 top.transient(parent)。否则主窗口 withdraw()（隐藏）时，
    # transient 的弹窗也跟着隐藏，导致界面看起来「启动了却没有窗口」。
    top.grab_set()

    top.update_idletasks()
    w, h = 500, 300
    x = (top.winfo_screenwidth() - w) // 2
    y = (top.winfo_screenheight() - h) // 2
    top.geometry(f"{w}x{h}+{x}+{y}")
    top.lift()          # 置顶
    top.focus_force()   # 强制聚焦，确保弹窗在最前面

    tk.Label(top, text="开源声明", font=("Microsoft YaHei", 14, "bold"),
             bg="white").pack(pady=(18, 8))

    body = (
        "欢迎使用 " + PROJECT_NAME + "（开源项目）\n\n"
        "本项目遵循 GNU GPL v3 开源许可证发布，源码已在 GitHub 公开。\n"
        "你可自由使用、修改与分发，但任何分发（含修改版）\n"
        "都必须保留版权声明，并以 GPL v3 开源。\n\n"
        "项目主页："
    )
    tk.Label(top, text=body, justify="center", bg="white",
             font=("Microsoft YaHei", 10)).pack(padx=26, pady=4)

    tk.Button(top, text=PROJECT_URL, fg="#1a6bdd", bg="white", bd=0,
              activebackground="white", activeforeground="#1a6bdd",
              cursor="hand2", font=("Microsoft YaHei", 10, "underline"),
              command=lambda: webbrowser.open(PROJECT_URL)).pack(pady=2)

    btns = tk.Frame(top, bg="white")
    btns.pack(pady=16)

    tk.Button(btns, text="打开 GitHub 项目页", width=18,
              command=lambda: webbrowser.open(PROJECT_URL)).pack(side="left", padx=6)
    tk.Button(btns, text="我知道了，继续", width=18,
              command=top.destroy).pack(side="left", padx=6)

    top.wait_window()


# ---------------------------------------------------------------------------
# 代理运行器（进程内 mitmproxy，跑在独立线程）
# ---------------------------------------------------------------------------
class ProxyRunner:
    def __init__(self, host, port, out_file, log_cb, seen_log="", debug_dir=""):
        self.host = host
        self.port = port
        self.out_file = out_file
        self.log_cb = log_cb
        self.seen_log = seen_log
        self.debug_dir = debug_dir
        self._master = None
        self._thread = None
        self._running = threading.Event()
        self._err = None

    def is_running(self):
        return self._running.is_set()

    async def _aio(self):
        from mitmproxy import options as mpo
        from mitmproxy.tools.dump import DumpMaster

        o = mpo.Options(
            listen_host=self.host,
            listen_port=self.port,
            http2=True,
            upstream_cert=False,  # 避免使用上游(抖音)证书，确保走我们的 MITM 证书
        )
        m = DumpMaster(o, with_termlog=False, with_dumper=False)
        m.addons.add(CaptureAddon(out_file=self.out_file, log=self.log_cb,
                                  seen_log=self.seen_log, debug_dir=self.debug_dir))
        self._master = m
        self._running.set()
        await m.run()
        self._master = None
        self._running.clear()

    def _run(self):
        try:
            asyncio.run(self._aio())
        except Exception as e:
            self._err = str(e)
            self.log_cb(f"代理异常：{e}")
            self._running.clear()

    def start(self):
        if self._running.is_set():
            return True, "代理已在运行"
        self._err = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # 等待端口起来（最多 5 秒）
        deadline = time.time() + 5
        while time.time() < deadline:
            if self._err:
                return False, f"启动失败：{self._err}"
            if self._is_listening():
                return True, "代理已启动"
            time.sleep(0.2)
        if self._is_listening():
            return True, "代理已启动"
        return False, "代理启动超时（端口可能被占用或启动异常）"

    def stop(self, quiet=False):
        m = self._master
        if m:
            try:
                m.should_exit.set()
            except Exception:
                pass
        if not quiet:
            self.log_cb("正在停止代理……")
        return not self._is_listening() or self._master is None

    def _is_listening(self):
        try:
            with socket.create_connection((self.host, self.port), timeout=0.5):
                return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# 界面
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("OBS权限助手 · 抖音直播伴侣推流码获取器")
        root.geometry("760x680")
        root.minsize(680, 620)

        os.makedirs(DATA_DIR, exist_ok=True)

        self.proxy_cb_queue = queue.Queue()
        self.runner = None
        self.auto_finish = tk.BooleanVar(value=False)
        self.restore_on_exit = tk.BooleanVar(value=True)
        self.debug_dump = tk.BooleanVar(value=False)

        self._build_ui()
        self._check_deps()
        self._drain_log()
        self._poll()

    def _check_deps(self):
        try:
            import mitmproxy  # noqa: F401
            self._deps_ok = True
        except Exception:
            self._deps_ok = False
            messagebox.showwarning(
                "缺少依赖",
                "未检测到 mitmproxy。\n请在本程序目录运行：\npip install -r requirements.txt\n",
            )

    # ----------------------- UI -----------------------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(4, weight=1)

        # 标题
        title = ttk.Label(
            self.root,
            text="OBS 权限助手\n在抖音直播伴侣里 一键获取 OBS 推流码",
            font=("Microsoft YaHei UI", 14, "bold"),
            anchor="center",
        )
        title.grid(row=0, column=0, sticky="ew", pady=(10, 4))

        # 参数区
        frame_cfg = ttk.LabelFrame(self.root, text="代理参数")
        frame_cfg.grid(row=1, column=0, sticky="ew", **pad)

        self.port_var = tk.StringVar(value="8080")
        self.host_var = tk.StringVar(value="127.0.0.1")
        self.proc_var = tk.StringVar(value="直播伴侣.exe")
        self.result_var = tk.StringVar(value=RESULT_FILE)

        ttk.Label(frame_cfg, text="监听端口").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(frame_cfg, textvariable=self.port_var, width=10).grid(row=0, column=1, **pad)
        ttk.Label(frame_cfg, text="代理主机").grid(row=0, column=2, sticky="e", **pad)
        ttk.Entry(frame_cfg, textvariable=self.host_var, width=14).grid(row=0, column=3, **pad)
        ttk.Label(frame_cfg, text="直播伴侣进程名").grid(row=1, column=0, sticky="e", **pad)
        ttk.Entry(frame_cfg, textvariable=self.proc_var, width=24).grid(row=1, column=1, columnspan=3, sticky="w", **pad)
        ttk.Label(frame_cfg, text="结果文件").grid(row=2, column=0, sticky="e", **pad)
        ttk.Entry(frame_cfg, textvariable=self.result_var, width=40).grid(row=2, column=1, columnspan=3, sticky="w", **pad)

        # 步骤按钮
        frame_btn = ttk.LabelFrame(self.root, text="操作步骤")
        frame_btn.grid(row=2, column=0, sticky="ew", **pad)

        self.btn_proxy = ttk.Button(frame_btn, text="① 启动代理", command=self.on_toggle_proxy)
        self.btn_proxy.grid(row=0, column=0, **pad)
        ttk.Button(frame_btn, text="② 安装证书", command=self.on_install_cert).grid(row=0, column=1, **pad)
        ttk.Button(frame_btn, text="③ 设置系统代理", command=self.on_set_proxy).grid(row=0, column=2, **pad)
        ttk.Button(frame_btn, text="④ 还原系统代理", command=self.on_restore_proxy).grid(row=1, column=0, **pad)
        ttk.Button(frame_btn, text="⑤ 结束直播伴侣", command=self.on_kill).grid(row=1, column=1, **pad)

        ttk.Checkbutton(frame_btn, text="抓到后自动收尾(结束直播伴侣+停代理+还原代理)",
                        variable=self.auto_finish).grid(row=1, column=2, **pad)
        ttk.Checkbutton(frame_btn, text="关闭时自动还原系统代理", variable=self.restore_on_exit)\
            .grid(row=1, column=3, **pad)
        ttk.Checkbutton(frame_btn, text="开启排错抓包(导出请求体到debug目录)",
                        variable=self.debug_dump).grid(row=2, column=0, columnspan=4, sticky="w", **pad)

        # 结果区
        frame_res = ttk.LabelFrame(self.root, text="捕获结果（填入 OBS）")
        frame_res.grid(row=3, column=0, sticky="ew", **pad)

        self.server_var = tk.StringVar(value="")
        self.key_var = tk.StringVar(value="")
        self.url_var = tk.StringVar(value="")

        self._res_row(frame_res, 0, "OBS 服务器地址", self.server_var)
        self._res_row(frame_res, 1, "OBS 串流密钥", self.key_var)
        self._res_row(frame_res, 2, "完整推流地址(仅供参考)", self.url_var)

        # 日志
        frame_log = ttk.LabelFrame(self.root, text="运行日志")
        frame_log.grid(row=4, column=0, sticky="nsew", **pad)
        frame_log.columnconfigure(0, weight=1)
        frame_log.rowconfigure(0, weight=1)
        self.log_box = scrolledtext.ScrolledText(frame_log, height=12, state="disabled", wrap="word")
        self.log_box.grid(row=0, column=0, sticky="nsew", **pad)

    def _res_row(self, parent, row, label, var):
        pad = {"padx": 6, "pady": 3}
        ttk.Label(parent, text=label, width=16, anchor="e",
                  font=("Microsoft YaHei UI", 10, "bold")).grid(row=row, column=0, sticky="e", **pad)
        ent = ttk.Entry(parent, textvariable=var)
        ent.grid(row=row, column=1, sticky="ew", **pad)
        parent.columnconfigure(1, weight=1)
        ttk.Button(parent, text="复制", command=lambda: self._copy(var.get()),
                   width=6).grid(row=row, column=2, **pad)

    def _copy(self, val):
        self.root.clipboard_clear()
        self.root.clipboard_append(val or "")
        self.log(f"已复制到剪贴板：{val[:40] or '(空)'}")

    # ----------------------- 日志 -----------------------
    def log(self, msg):
        self.proxy_cb_queue.put(str(msg))

    def _drain_log(self):
        try:
            while True:
                msg = self.proxy_cb_queue.get_nowait()
                self.log_box.configure(state="normal")
                self.log_box.insert("end", time.strftime("[%H:%M:%S] ") + msg + "\n")
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(150, self._drain_log)

    # ----------------------- 动作 -----------------------
    def on_toggle_proxy(self):
        if not getattr(self, "_deps_ok", True):
            messagebox.showerror("缺少依赖", "请先安装 mitmproxy：pip install -r requirements.txt")
            return
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self.btn_proxy.config(text="① 启动代理")
            self.log("代理已停止")
            return
        port = self._port()
        if port is None:
            return
        if self._port_in_use(port, self.host_var.get()):
            messagebox.showerror("端口占用", f"端口 {self.host_var.get()}:{port} 已被占用，请改用其它端口。")
            return
        self.runner = ProxyRunner(
            self.host_var.get().strip() or "127.0.0.1",
            port, self.result_var.get().strip() or RESULT_FILE,
            self.log,
            seen_log=SEEN_FLOW_FILE,
            debug_dir=DEBUG_DUMP_DIR if self.debug_dump.get() else "",
        )
        ok, msg = self.runner.start()
        self.log(msg)
        if ok:
            self.btn_proxy.config(text="① 停止代理")
            self.log("匹配到的抖音HTTP流会记录到：" + SEEN_FLOW_FILE)
            if self.debug_dump.get():
                self.log("排错抓包已开启，请求体保存在：" + DEBUG_DUMP_DIR)
            # 首次运行会自动生成 CA 证书
            cert = proxy_utils.find_mitm_ca_cert()
            if cert:
                self.log(f"CA 证书已就绪：{cert}")
                self.log("请点击【② 安装证书】，随后【③ 设置系统代理】。")
            else:
                self.log("未找到 CA 证书，请稍后重试安装证书。")

    def on_install_cert(self):
        cert = proxy_utils.find_mitm_ca_cert()
        if not cert:
            self.log("未找到 CA 证书。请先点击【① 启动代理】，让它生成证书。")
            messagebox.showinfo("提示", "未找到 CA 证书，请先启动代理一次。")
            return
        ok = proxy_utils.install_ca_cert(cert)
        msg = "证书已安装到「受信任的根证书颁发机构」" if ok else "证书安装失败（可能是未批准 UAC 或已有同名证书）。"
        self.log(msg)
        if ok:
            # 证书已有，提示继续
            self.log("现在点击【③ 设置系统代理】。")

    def on_set_proxy(self):
        port = self._port()
        if port is None:
            return
        host = self.host_var.get().strip() or "127.0.0.1"
        msg = proxy_utils.set_system_proxy(host, port)
        self.log(msg)
        if "已设置" in msg:
            self.log("现在打开【抖音直播伴侣】，登录并点击【开始直播】。")

    def on_restore_proxy(self):
        self.log(proxy_utils.restore_system_proxy())

    def on_kill(self):
        name = self.proc_var.get().strip() or "直播伴侣.exe"
        self.log(proxy_utils.kill_process(name))

    # ----------------------- 轮询 -----------------------
    def _poll(self):
        if not hasattr(self, "_last_capture_time"):
            self._last_capture_time = ""
        data = None
        try:
            with open(self.result_var.get().strip() or RESULT_FILE, "r", encoding="utf-8") as f:
                raw = f.read()
            if raw.strip():
                data = json.loads(raw)
        except Exception:
            data = None

        if data and data.get("found") and data.get("time") != self._last_capture_time:
            self._last_capture_time = data.get("time", "")
            self.server_var.set(data.get("server", ""))
            self.key_var.set(data.get("stream_key", ""))
            self.url_var.set(data.get("push_url", ""))
            src = data.get("source", "")
            self.log(f"★ 捕获到推流信息（来源：{src}），已填入上方，请复制到 OBS！")
            if self.auto_finish.get():
                self._finish_after_capture()
            else:
                self.log("提示：直播伴侣仍在推流。请在 OBS 填好服务器+串流密钥并【开始推流】；")
                self.log("       OBS 连上后，再点⑤【结束直播伴侣】，然后①停止代理、④还原系统代理。")
        self.root.after(1000, self._poll)

    def _finish_after_capture(self):
        """捕获成功后自动收尾：结束直播伴侣、停代理、还原系统代理，让 OBS 接管。"""
        self.log("── 自动收尾 ──")
        self.on_kill()
        if self.runner and self.runner.is_running():
            self.runner.stop(quiet=True)
            self.btn_proxy.config(text="① 启动代理")
            self.log("代理已停止")
        self.log(proxy_utils.restore_system_proxy())
        self.log("现在在 OBS 里填入上面的【服务器地址】+【串流密钥】并开始推流即可。")

    # ----------------------- 工具 & 关闭 -----------------------
    def _port(self):
        try:
            p = int(self.port_var.get())
            if not (1 <= p <= 65535):
                raise ValueError
            return p
        except Exception:
            messagebox.showerror("端口错误", "端口必须是 1-65535 的整数。")
            return None

    def _port_in_use(self, port, host):
        try:
            with socket.create_connection((host or "127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    def on_close(self):
        if self.restore_on_exit.get():
            try:
                proxy_utils.restore_system_proxy()
            except Exception:
                pass
        if self.runner and self.runner.is_running():
            try:
                self.runner.stop(quiet=True)
            except Exception:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    root.withdraw()                # 先隐藏主窗口
    show_open_source_notice(root)  # 弹开源声明，确认后继续
    app = App(root)
    root.deiconify()               # 显示主窗口
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
