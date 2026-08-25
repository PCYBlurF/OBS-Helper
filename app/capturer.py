# -*- coding: utf-8 -*-
"""
capturer.py
===========
抖音直播伴侣「推流码」捕获插件（mitmproxy addon）。

原理：
    通过 HTTPS 中间人代理，在用户点击「开始直播」时，直播伴侣会向抖音服务器
    上报 / 拉取推流信息。这里拦截两类数据：
      1) 开播接口     -> /webcast/room/create/  (响应体里 data.stream_url.rtmp_push_url)
      2) 上报日志     -> log-snssdk.zijieapi.com/video/v1/live_log/ (请求体里 push_url)
    只要在流量里出现 rtmp:// 开头的推流地址，就把它解析成
        OBS 服务器地址 + 串流密钥
    并写到指定的结果文件里供 GUI 读取。

用法（由 GUI 启动，不要单独手动跑）：
    mitmdump -q -s capturer.py --listen-host 127.0.0.1 --listen-port <PORT>
    结果文件路径通过环境变量 OBS_HELPER_OUT 指定。
"""

import json
import os
import re
import threading
import time
from mitmproxy import http

# 会重点关注的域名后缀。直播伴侣的推流信息基本都来自这些域。
WATCH_DOMAIN_SNIPPETS = (
    "douyin.com",
    "amemv.com",
    "zijieapi.com",
    "bytedance.com",
    "byteimg.com",
)

# 开播接口的路径关键字
ROOM_CREATE_MARKERS = ("/webcast/room/create/", "room/create", "room_create")
# 上报日志的路径
LIVE_LOG_MARKERS = ("/video/v1/live_log/", "live_log", "/live_log/")




def _looks_like_douyin(host: str) -> bool:
    hl = (host or "").lower()
    return any(s in hl for s in WATCH_DOMAIN_SNIPPETS)


def _walk_rtmp_strings(obj):
    """递归遍历 JSON，收集所有以 rtmp 开头的字符串。"""
    found = []
    if isinstance(obj, str):
        if obj.startswith("rtmp://") or obj.startswith("rtmps://"):
            found.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(_walk_rtmp_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_walk_rtmp_strings(v))
    return found


def _regex_rtmp(text: str):
    """从纯文本里用正则捞 rtmp 地址（防止 JSON 解析失败）。"""
    return re.findall(r"rtmp[s]?://[^\s\"'<>\\]+", text)


def _extract_rtmp_candidates(text: str, host: str, path: str):
    """返回 (候选列表, 是否来自开播接口)。"""
    cands = []
    from_room_create = False

    if any(m in (path or "") for m in ROOM_CREATE_MARKERS):
        from_room_create = True

    try:
        data = json.loads(text)
        cands.extend(_walk_rtmp_strings(data))
    except Exception:
        pass

    # 文本兜底
    for u in _regex_rtmp(text):
        if u not in cands:
            cands.append(u)

    return cands, from_room_create


def split_push_url(url: str):
    """
    把完整推流地址拆成 (OBS服务器地址, 串流密钥)。

    抖音的地址格式一般是:
        rtmp://<host>/third/<push-xxx>?<签名参数>
    对应 OBS 自定义服务器 + 串流密钥，OBS 会以 <server>/<key> 重新拼接回原文。
    """
    if not url:
        return "", ""

    # 1) 抖音最常见的 /third/ 形式
    m = re.match(r"^(rtmp[s]?://[^/]+/third/)(.+)$", url)
    if m:
        server = m.group(1).rstrip("/")   # rtmp://host/third
        key = m.group(2)                   # push-xxx?txSecret=...&txTime=...
        return server, key

    # 2) 通用拆分：服务器 = scheme://host + 倒数第一个目录，串流密钥 = 最后一个路径段(+query)
    try:
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        path = parsed.path.rstrip("/")
        idx = path.rfind("/")
        if idx == -1:
            return url, ""
        server_path = path[:idx].rstrip("/")   # 形如 /live
        key = path[idx + 1:]
        if parsed.query:
            key += "?" + parsed.query
        server = parsed.scheme + "://" + parsed.netloc
        if server_path:
            server += server_path      # server_path 以 / 开头
        return server, key
    except Exception:
        return url, ""


def _pick_best(cands):
    """从多个候选里挑一个最像推流地址的。"""
    if not cands:
        return ""
    # 优先含 /third/ 或含 push 的
    for c in cands:
        if "/third/" in c:
            return c
    for c in cands:
        if "push" in c:
            return c
    return cands[0]


def _emit(push_url: str, host: str, path: str, source: str, out_file: str, debug=None):
    server, key = split_push_url(push_url)
    res = {
        "found": bool(push_url),
        "push_url": push_url,
        "server": server,
        "stream_key": key,
        "host": host,
        "path": path,
        "source": source,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if debug:
        res["debug"] = debug
    if out_file:
        try:
            tmp = out_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            os.replace(tmp, out_file)
        except Exception:
            pass
    # 同时打印到标准输出，方便单独排查
    if push_url:
        print(f"[CAPTURED] {source} server={server} key={key}")


class CaptureAddon:
    """mitmproxy 插件：抓取 rtmp 推流地址。"""

    _seen_lock = threading.Lock()

    def __init__(self, out_file="", log=print, seen_log="", debug_dir=""):
        # out_file: 结果写到哪里；为空则只打印
        self.out_file = out_file or os.environ.get("OBS_HELPER_OUT", "")
        self.log = log
        self.seen_log = seen_log or os.environ.get("OBS_HELPER_SEEN", "")
        self.debug_dir = debug_dir or os.environ.get("OBS_HELPER_DEBUG", "")
        # 用于去重，避免重复写文件
        self._last = None
        self._last_ts = 0.0

    def _record_flow(self, flow, kind):
        """记录匹配到的抖音 HTTP 流；可选导出请求体。"""
        host = flow.request.host
        path = flow.request.path
        method = flow.request.method
        ctype = ""
        body_len = 0
        try:
            if kind == "response":
                ctype = flow.response.headers.get("content-type", "")
                body_len = len(flow.response.content or b"")
            else:
                ctype = flow.request.headers.get("content-type", "")
                body_len = len(flow.request.content or b"")
        except Exception:
            pass

        if self.seen_log:
            try:
                with self._seen_lock:
                    with open(self.seen_log, "a", encoding="utf-8") as f:
                        f.write(
                            f"{time.strftime('%H:%M:%S')} {method} {host}{path} "
                            f"[{ctype}] len={body_len}\n"
                        )
            except Exception:
                pass

        if self.debug_dir and kind == "request":
            try:
                os.makedirs(self.debug_dir, exist_ok=True)
                safe = re.sub(r"[^\w\-.]", "_", f"{host}{path}")[:120]
                fn = os.path.join(self.debug_dir,
                                  f"{time.strftime('%Y%m%d%H%M%S')}_{safe}.body")
                with open(fn, "wb") as f:
                    f.write(flow.request.content or b"")
            except Exception:
                pass

    def _emit(self, push_url, host, path, source, debug=None):
        server, key = split_push_url(push_url)
        res = {
            "found": bool(push_url),
            "push_url": push_url,
            "server": server,
            "stream_key": key,
            "host": host,
            "path": path,
            "source": source,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if debug:
            res["debug"] = debug
        if self.out_file:
            try:
                tmp = self.out_file + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(res, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.out_file)
            except Exception:
                pass
        if push_url:
            self.log(f"[捕获] {source} 服务器={server} 密钥={key}")

    def _handle_text(self, text: str, host: str, path: str, is_log: bool,
                     source: str, debug_key: str):
        if not text:
            return
        cands, _from_room = _extract_rtmp_candidates(text, host, path)
        if not cands:
            return

        push_url = _pick_best(cands)
        if not push_url:
            return

        # 去重：同一地址短时间内只报一次
        now = time.time()
        if push_url == self._last and now - self._last_ts < 10:
            return
        self._last = push_url
        self._last_ts = now

        debug = [{"host": host, "path": path, "len": len(text)}]
        self._emit(push_url, host, path, source, debug)

    def request(self, flow: http.HTTPFlow):
        """处理请求体（上报日志类）。"""
        host = flow.request.pretty_host
        path = flow.request.path
        if not _looks_like_douyin(host):
            return
        self._record_flow(flow, "request")
        if any(m in path for m in LIVE_LOG_MARKERS):
            try:
                text = flow.request.get_text(strict=False)
            except Exception:
                text = ""
            # 上报日志里 push_url 在 push_stream 事件里
            self._handle_text(text, host, path, True,
                              source="上报日志", debug_key="req")

    def response(self, flow: http.HTTPFlow):
        """处理响应体（开播接口等）。"""
        host = flow.request.pretty_host
        path = flow.request.path
        if not _looks_like_douyin(host):
            return
        # 只处理内容类型为 json / text 的，避免去解图片音频
        content_type = flow.response.headers.get("content-type", "")
        if not any(t in content_type for t in ("json", "text")):
            return
        try:
            text = flow.response.get_text(strict=False)
        except Exception:
            return
        if not text:
            return
        self._record_flow(flow, "response")

        is_room = any(m in path for m in ROOM_CREATE_MARKERS)
        is_log = any(m in path for m in LIVE_LOG_MARKERS)
        if is_room or is_log:
            self._handle_text(text, host, path, is_log,
                              source="开播接口" if is_room else "上报日志",
                              debug_key="resp")


def make_addon(out_file="", log=print):
    return CaptureAddon(out_file=out_file, log=log)


def default_addons():
    return [CaptureAddon()]


# 兼容直接 `mitmdump -s capturer.py` 的方式
addons = [CaptureAddon()]
