# -*- coding: utf-8 -*-
"""
proxy_utils.py
==============
Windows 系统侧的管理工具：
  * 定位 / 安装 mitmproxy CA 根证书
  * 设置 / 还原系统 HTTP(S) 代理
  * 结束直播伴侣进程
只依赖标准库。
"""

import ctypes
import json
import os
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# 管理
# ---------------------------------------------------------------------------
def _state_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), ".obs_helper")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.dirname(os.path.abspath(__file__))
    return d


PROXY_STATE_FILE = os.path.join(_state_dir(), "_proxy_state.json")


def is_admin() -> bool:
    """当前进程是否以管理员身份运行。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _elevate(args: str) -> bool:
    """弹 UAC 提权运行一条命令行（args 为 cmd 参数）。返回是否成功发起。"""
    try:
        # runas 启动 cmd /c <args>
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "cmd.exe", f"/c {args}", None, 1
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CA 证书
# ---------------------------------------------------------------------------
def find_mitm_ca_cert() -> str:
    """返回 mitmproxy 生成的 CA 根证书路径 (.cer)，找不到则返回空字符串。"""
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".mitmproxy", "mitmproxy-ca-cert.cer"),
        os.path.join(home, ".mitmproxy", "mitmproxy-ca-cert.pem"),
        # 若用我们自定义 confdir
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "mitmproxy_conf", "mitmproxy-ca-cert.cer"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return ""


def install_ca_cert(cert_path: str) -> bool:
    """把 CA 根证书安装到「本机受信任的根证书颁发机构」。需要管理员。"""
    if not cert_path or not os.path.isfile(cert_path):
        return False
    if is_admin():
        r = subprocess.run(
            ["certutil", "-addstore", "-f", "Root", cert_path],
            capture_output=True, text=True,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0 or ("已添加" in out) or ("add" in out.lower())
    # 非管理员：提权运行 certutil，把证书路径作为一个参数
    return _elevate(f'certutil -addstore -f Root "{cert_path}"')


# ---------------------------------------------------------------------------
# 系统代理（HKCU 注册表）
# ---------------------------------------------------------------------------
PROXY_KEYS = {
    "ProxyEnable": 1,
    "ProxyServer": None,  # 运行时填充
    "ProxyOverride": "<local>;localhost;127.*;192.168.*",
    "AutoConfigURL": None,
}


def _read_proxy_reg():
    import winreg
    vals = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                             0, winreg.KEY_READ)
    except Exception:
        return vals
    for name in ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL"):
        try:
            v, _ = winreg.QueryValueEx(key, name)
            vals[name.strip()] = v
        except Exception:
            pass
    winreg.CloseKey(key)
    return vals


def _write_proxy_reg(vals: dict):
    import winreg
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        0, winreg.KEY_SET_VALUE,
    )
    for name, v in vals.items():
        if v is None or v == "":
            try:
                winreg.DeleteValue(key, name)
            except Exception:
                pass
        elif isinstance(v, int):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, v)
        else:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(v))
    winreg.CloseKey(key)


def _refresh_wininet():
    """通知系统刷新 WinINet 代理设置。"""
    try:
        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        internet_set_option = ctypes.windll.wininet.InternetSetOptionW
        internet_set_option(None, INTERNET_OPTION_SETTINGS_CHANGED, None, 0)
        internet_set_option(None, INTERNET_OPTION_REFRESH, None, 0)
    except Exception:
        pass


def set_system_proxy(host: str, port: int) -> str:
    """设置系统代理并保存原始值，返回状态字符串（成功/失败原因）。"""
    try:
        old = _read_proxy_reg()
        with open(PROXY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({k: (str(v) if not isinstance(v, int) else v)
                       for k, v in old.items()}, f, ensure_ascii=False)

        new = {
            "ProxyEnable": 1,
            "ProxyServer": f"{host}:{port}",
            "ProxyOverride": PROXY_KEYS["ProxyOverride"],
            "AutoConfigURL": "",
        }
        _write_proxy_reg(new)
        _refresh_wininet()
        return f"系统代理已设置为 {host}:{port}"
    except Exception as e:
        return f"设置系统代理失败：{e}"


def restore_system_proxy() -> str:
    """根据保存的状态还原系统代理。"""
    # 如果从未调用过 set_system_proxy（没有保存的状态），不要动系统代理
    if not os.path.isfile(PROXY_STATE_FILE):
        return "未设置过系统代理，无需还原"
    try:
        old = {}
        if os.path.isfile(PROXY_STATE_FILE):
            with open(PROXY_STATE_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)

        vals = {
            "ProxyEnable": int(old.get("ProxyEnable", 0)),
            "ProxyServer": old.get("ProxyServer", ""),
            "ProxyOverride": old.get("ProxyOverride", ""),
            "AutoConfigURL": old.get("AutoConfigURL", ""),
        }
        _write_proxy_reg(vals)
        _refresh_wininet()
        return "系统代理已还原"
    except Exception as e:
        return f"还原系统代理失败：{e}"


# ---------------------------------------------------------------------------
# 结束直播伴侣进程
# ---------------------------------------------------------------------------
def kill_process(name: str) -> str:
    """结束指定进程（默认直播伴侣）。返回结果说明。

    普通权限杀不掉高权限进程时（拒绝访问 / Access is denied），会主动弹 UAC
    提权重试一次；只有真正需要时才提权。
    """
    name = (name or "直播伴侣.exe").strip()
    try:
        r = subprocess.run(["taskkill", "/F", "/IM", name],
                           capture_output=True, text=True)
    except Exception as e:
        return f"结束进程失败：{e}"

    if r.returncode == 0:
        return f"已结束进程：{name}"

    out = (r.stdout or r.stderr or "").strip()
    low = out.lower()
    denied = ("拒绝访问" in out or "access is denied" in low
              or "denied" in low or "拒绝" in out)

    # 权限不足且当前未提权：UAC 提权重试
    if denied and not is_admin():
        if _elevate(f'taskkill /F /IM "{name}"'):
            return (f"已请求管理员权限结束进程：{name}。\n"
                    "若没有弹出 UAC 窗口，请右键本程序「以管理员身份运行」后再点一次。")
        return f"结束进程失败（权限不足）：{out}"

    return f"未结束进程 {name}\n{out}"
