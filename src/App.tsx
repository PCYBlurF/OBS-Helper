import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import "./App.css";

interface Capture {
  server: string;
  stream_key: string;
  push_url: string;
  source: string;
  time: number;
}

interface LogEntry {
  level: "info" | "ok" | "err";
  msg: string;
  ts: string;
}

function ts() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

export default function App() {
  const [proxyRunning, setProxyRunning] = useState(false);
  const [caReady, setCaReady] = useState(false);
  const [proxySet, setProxySet] = useState(false);
  const [admin, setAdmin] = useState(true);
  const [host, setHost] = useState("127.0.0.1");
  const [port, setPort] = useState(8080);
  const [restoreOnExit, setRestoreOnExit] = useState(true);
  const [capture, setCapture] = useState<Capture | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const logBox = useRef<HTMLDivElement>(null);

  const addLog = useCallback((msg: string, level: LogEntry["level"] = "info") => {
    setLogs((prev) => [...prev, { level, msg, ts: ts() }].slice(-300));
  }, []);

  useEffect(() => {
    invoke<boolean>("is_admin")
      .then(setAdmin)
      .catch(() => setAdmin(true));
    invoke<Capture | null>("get_capture")
      .then((c) => c && setCapture(c))
      .catch(() => {});

    const unlistenCapture = listen<Capture>("flow-captured", (e) => {
      setCapture(e.payload);
      addLog(`抓到推流地址（来源：${e.payload.source}）`, "ok");
    });
    const unlistenLog = listen<string>("log", (e) => {
      addLog(e.payload);
    });

    return () => {
      unlistenCapture.then((f) => f());
      unlistenLog.then((f) => f());
    };
  }, [addLog]);

  useEffect(() => {
    invoke("set_restore_on_exit", { enabled: restoreOnExit }).catch(() => {});
  }, [restoreOnExit]);

  useEffect(() => {
    if (logBox.current) {
      logBox.current.scrollTop = logBox.current.scrollHeight;
    }
  }, [logs]);

  const run = async (label: string, fn: () => Promise<string>) => {
    addLog(`▶ ${label}`);
    try {
      const msg = await fn();
      addLog(msg, "ok");
      return true;
    } catch (err) {
      addLog(`✕ ${label} 失败：${err}`, "err");
      return false;
    }
  };

  const startProxy = () =>
    run("① 启动代理", () => invoke<string>("start_proxy", { host, port }).then((m) => { setProxyRunning(true); return m; }));
  const stopProxy = () =>
    run("停止代理", () => invoke<string>("stop_proxy").then((m) => { setProxyRunning(false); return m; }));
  const doInstallCert = () =>
    run("② 安装证书", () => invoke<string>("install_cert").then((m) => { setCaReady(true); return m; }));
  const doSetProxy = () =>
    run("③ 设置系统代理", () => invoke<string>("set_proxy", { host, port }).then((m) => { setProxySet(true); return m; }));
  const doRestoreProxy = () =>
    run("④ 还原系统代理", () => invoke<string>("restore_proxy").then((m) => { setProxySet(false); return m; }));
  const doKill = () =>
    run("⑤ 结束直播伴侣", () => invoke<string>("kill_process", { name: "直播伴侣.exe" }));
  const copy = (text: string, label: string) =>
    run(`复制${label}`, () => invoke<string>("copy_text", { text }));

  const status = (on: boolean) => <span className={`dot ${on ? "on" : ""}`} />;

  return (
    <div className="app">
      <header>
        <h1>OBS 权限助手</h1>
        <div className="badges">
          <span className={proxyRunning ? "badge ok" : "badge"}>
            {status(proxyRunning)} 代理 {proxyRunning ? "运行中" : "未启动"}
          </span>
          <span className={caReady ? "badge ok" : "badge"}>
            {status(caReady)} 证书 {caReady ? "已就绪" : "未安装"}
          </span>
          <span className={proxySet ? "badge ok" : "badge"}>
            {status(proxySet)} 系统代理 {proxySet ? "已设置" : "未设置"}
          </span>
        </div>
      </header>

      <section className="card result">
        <div className="card-title">抓取结果</div>
        {capture ? (
          <div className="result-grid">
            <div className="field">
              <label>服务器地址</label>
              <div className="value-row">
                <code>{capture.server}</code>
                <button onClick={() => copy(capture.server, "服务器地址")}>复制</button>
              </div>
            </div>
            <div className="field">
              <label>串流密钥</label>
              <div className="value-row">
                <code>{capture.stream_key}</code>
                <button onClick={() => copy(capture.stream_key, "串流密钥")}>复制</button>
              </div>
            </div>
            <div className="field">
              <label>完整推流地址</label>
              <div className="value-row">
                <code>{capture.push_url}</code>
                <button onClick={() => copy(capture.push_url, "完整推流地址")}>复制</button>
              </div>
            </div>
            <div className="field hint">
              请在 OBS「设置 → 推流」中选择「自定义」，将上方「服务器地址」与「串流密钥」填入。推流码有时效，请尽快使用。
            </div>
          </div>
        ) : (
          <div className="empty">
            暂无结果。启动代理、安装证书并设置系统代理后，打开 抖音直播伴侣 并点击开始直播，这里会自动显示推流码。
          </div>
        )}
      </section>

      <section className="card steps">
        <div className="card-title">操作步骤</div>
        <div className="step-row">
          <button className="step" onClick={startProxy} disabled={proxyRunning}>
            ① 启动代理
          </button>
          <button className="step" onClick={doInstallCert}>
            ② 安装证书
          </button>
          <button className="step" onClick={doSetProxy}>
            ③ 设置系统代理
          </button>
          <button className="step" onClick={doRestoreProxy}>
            ④ 还原系统代理
          </button>
          <button className="step" onClick={doKill}>
            ⑤ 结束直播伴侣
          </button>
        </div>
        <div className="step-row">
          <button className="ghost" onClick={stopProxy} disabled={!proxyRunning}>
            停止代理
          </button>
          {!admin && <span className="warn">当前非管理员运行，部分操作会触发 UAC 提权</span>}
        </div>
      </section>

      <section className="card settings">
        <div className="card-title">设置</div>
        <div className="settings-row">
          <label>
            监听地址
            <input value={host} onChange={(e) => setHost(e.target.value)} />
          </label>
          <label>
            端口
            <input type="number" value={port} onChange={(e) => setPort(Number(e.target.value) || 8080)} />
          </label>
          <label className="chk">
            <input
              type="checkbox"
              checked={restoreOnExit}
              onChange={(e) => setRestoreOnExit(e.target.checked)}
            />
            退出时自动还原系统代理
          </label>
        </div>
        <div className="note">
          抓取到推流码后，请先还原系统代理（④），再用 OBS 推流——RTMP 不经过本机代理。抓图成功后不会自动结束直播伴侣，避免直播伴侣在 OBS 连接前退出导致推流地址失效。
        </div>
      </section>

      <section className="card logcard">
        <div className="card-title">日志</div>
        <div className="log" ref={logBox}>
          {logs.length === 0 && <div className="empty">暂无日志。</div>}
          {logs.map((l, i) => (
            <div key={i} className={`log-line ${l.level}`}>
              <span className="ts">{l.ts}</span> {l.msg}
            </div>
          ))}
        </div>
      </section>

      <footer>本工具仅供个人学习使用。第三方推流可能触发平台风控，请注意账号风险。</footer>
    </div>
  );
}
