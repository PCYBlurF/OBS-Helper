//! Native Rust HTTPS MITM proxy built on `hudsucker`. It intercepts Douyin
//! traffic, recovers the RTMP push URL from `room/create` responses and the
//! `live_log` request bodies, and emits a `flow-captured` Tauri event.

use hudsucker::certificate_authority::RcgenAuthority;
use hudsucker::hyper::{Request, Response};
use hudsucker::{
    Body, HttpContext, HttpHandler, Proxy, RequestOrResponse, decode_request, decode_response,
};
use http_body_util::BodyExt;
use regex::Regex;
use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::Emitter;

use crate::Capture;

const WATCH_SUFFIXES: &[&str] = &[
    "douyin.com",
    "amemv.com",
    "zijieapi.com",
    "bytedance.com",
    "byteimg.com",
];
const ROOM_CREATE_MARKERS: &[&str] =
    &["/webcast/room/create/", "room/create", "room_create"];
const LIVE_LOG_MARKERS: &[&str] = &["/video/v1/live_log/", "live_log", "/live_log/"];

/// The running proxy task plus a shut-down handle.
pub struct ProxyRunner {
    pub addr: SocketAddr,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
    task: tokio::task::JoinHandle<Result<(), hudsucker::Error>>,
}

impl ProxyRunner {
    pub async fn stop(mut self) {
        if let Some(s) = self.stop.take() {
            let _ = s.send(());
        }
        let _ = self.task.await;
    }
}

/// Build + bind + start the proxy. Binds eagerly so a port-in-use error
/// surfaces immediately instead of inside the spawned task.
pub async fn spawn_proxy(
    listener: tokio::net::TcpListener,
    ca: RcgenAuthority,
    app: tauri::AppHandle,
    last: Arc<Mutex<Option<Capture>>>,
) -> Result<ProxyRunner, String> {
    let bound = listener.local_addr().map_err(|e| format!("获取监听地址失败: {e}"))?;
    let (stop, done) = tokio::sync::oneshot::channel::<()>();
    let handler = Capturer::new(app, last);

    let proxy = Proxy::builder()
        .with_listener(listener)
        .with_ca(ca)
        .with_rustls_connector(hudsucker::rustls::crypto::aws_lc_rs::default_provider())
        .with_http_handler(handler)
        .with_graceful_shutdown(async move {
            let _ = done.await;
        })
        .build()
        .map_err(|e| format!("构建代理失败: {e}"))?;

    let task = tokio::spawn(proxy.start());
    Ok(ProxyRunner {
        addr: bound,
        stop: Some(stop),
        task,
    })
}

/// Capture handler: reads relevant request/response bodies, extracts the RTMP
/// push URL, and emits it to the frontend.
#[derive(Clone)]
pub struct Capturer {
    app: tauri::AppHandle,
    seen: Arc<Mutex<HashMap<String, u64>>>,
    /// Shared slot written so the frontend can later query the last capture.
    last: Arc<Mutex<Option<Capture>>>,
    /// Per client-connection last request target (host, path) so the response
    /// handler can decide whether a body is worth scanning.
    last_target: Arc<Mutex<HashMap<SocketAddr, (String, String)>>>,
}

impl Capturer {
    pub fn new(app: tauri::AppHandle, last: Arc<Mutex<Option<Capture>>>) -> Self {
        Self {
            app,
            seen: Arc::new(Mutex::new(HashMap::new())),
            last,
            last_target: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn log(&self, msg: impl Into<String>) {
        let _ = self.app.emit("log", msg.into());
    }

    fn emit_capture(&self, server: String, stream_key: String, push_url: String, source: &str) {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        {
            let mut seen = self.seen.lock().unwrap();
            if let Some(last) = seen.get(&push_url) {
                if now.saturating_sub(*last) < 10 {
                    return;
                }
            }
            seen.insert(push_url.clone(), now);
        }
        let cap = Capture {
            server,
            stream_key,
            push_url: push_url.clone(),
            source: source.into(),
            time: now,
        };
        {
            let mut last = self.last.lock().unwrap();
            *last = Some(cap.clone());
        }
        let _ = self.app.emit("flow-captured", &cap);
        self.log(format!(
            "【抓取成功】来源 {source} — 服务器: {} | 密钥: {}",
            cap.server, cap.stream_key
        ));
    }
}

impl HttpHandler for Capturer {
    async fn handle_request(&mut self, ctx: &HttpContext, req: Request<Body>) -> RequestOrResponse {
        let host = req.uri().host().unwrap_or("").to_string();
        let path = req.uri().path().to_string();
        let is_douyin = looks_douyin(&host);

        if is_douyin {
            if let Ok(mut m) = self.last_target.lock() {
                m.insert(ctx.client_addr, (host.clone(), path.clone()));
            }
        }

        let inspect =
            is_douyin && LIVE_LOG_MARKERS.iter().any(|m| path.contains(m)) && req.method() != hudsucker::hyper::Method::CONNECT;
        if !inspect {
            return req.into();
        }

        let decoded = match decode_request(req) {
            Ok(d) => d,
            Err(e) => {
                self.log(format!("解码请求失败: {e}"));
                return Request::new(Body::empty()).into();
            }
        };
        let (parts, body) = decoded.into_parts();
        let bytes = match body.collect().await {
            Ok(c) => c.to_bytes(),
            Err(e) => {
                self.log(format!("读取请求体失败: {e}"));
                let new_req = Request::from_parts(parts, Body::empty());
                return new_req.into();
            }
        };
        let text = String::from_utf8_lossy(&bytes).to_string();
        if let Some(url) = find_rtmp(&text) {
            if let Some((server, key)) = split_push_url(&url) {
                self.emit_capture(server, key, url.clone(), "live_log 请求");
            }
        }

        let mut new_req = Request::from_parts(parts, Body::from(bytes.clone()));
        fix_headers(new_req.headers_mut(), bytes.len());
        new_req.into()
    }

    async fn handle_response(&mut self, ctx: &HttpContext, res: Response<Body>) -> Response<Body> {
        let target = self.last_target.lock().unwrap().get(&ctx.client_addr).cloned();
        let (host, path) = match target {
            Some((h, p)) => (h, p),
            None => return res,
        };
        let is_douyin = looks_douyin(&host);
        let is_target =
            ROOM_CREATE_MARKERS.iter().any(|m| path.contains(m))
                || LIVE_LOG_MARKERS.iter().any(|m| path.contains(m));
        if !(is_douyin && is_target) {
            return res;
        }
        if !content_is_json_or_text(&res) {
            return res;
        }

        let decoded = match decode_response(res) {
            Ok(d) => d,
            Err(e) => {
                self.log(format!("解码响应失败: {e}"));
                return Response::new(Body::empty());
            }
        };
        let (parts, body) = decoded.into_parts();
        let bytes = match body.collect().await {
            Ok(c) => c.to_bytes(),
            Err(e) => {
                self.log(format!("读取响应体失败: {e}"));
                return Response::from_parts(parts, Body::empty());
            }
        };
        let text = String::from_utf8_lossy(&bytes).to_string();
        if let Some(url) = find_rtmp(&text) {
            if let Some((server, key)) = split_push_url(&url) {
                let source = if path.contains("room") { "room/create 响应" } else { "live_log 响应" };
                self.emit_capture(server, key, url.clone(), source);
            }
        }

        let mut new_res = Response::from_parts(parts, Body::from(bytes.clone()));
        fix_headers(new_res.headers_mut(), bytes.len());
        new_res
    }
}

fn looks_douyin(host: &str) -> bool {
    WATCH_SUFFIXES.iter().any(|s| host == *s || host.ends_with(*s))
}

fn content_is_json_or_text(res: &Response<Body>) -> bool {
    res.headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .map(|ct| ct.contains("json") || ct.contains("text"))
        .unwrap_or(false)
}

fn fix_headers(headers: &mut http::HeaderMap, len: usize) {
    use http::header::{CONTENT_ENCODING, CONTENT_LENGTH, TRANSFER_ENCODING};
    headers.remove(CONTENT_ENCODING);
    headers.remove(TRANSFER_ENCODING);
    if let Ok(v) = http::HeaderValue::from_str(&len.to_string()) {
        headers.insert(CONTENT_LENGTH, v);
    }
}

/// Look for RTMP URLs and pick the best candidate.
pub fn find_rtmp(text: &str) -> Option<String> {
    let re = Regex::new(r#"rtmps?://[^\s"'<>\\]+"#).ok()?;
    let mut cands: Vec<String> = re.find_iter(text).map(|m| m.as_str().to_string()).collect();
    // Also a naive scan as a safety net (some URLs split across JSON string escapes).
    if cands.is_empty() {
        let re2 = Regex::new(r"rtmps?://[A-Za-z0-9._\-:/?=&%]+").ok()?;
        cands = re2.find_iter(text).map(|m| m.as_str().to_string()).collect();
    }
    pick_best(&cands)
}

/// Score candidates: prefer paths containing `third`, then hosts containing
/// `push`, then `douyincdn`, otherwise the first found.
fn pick_best(cands: &[String]) -> Option<String> {
    if cands.is_empty() {
        return None;
    }
    cands
        .iter()
        .max_by_key(|c| {
            let mut s = 0;
            if c.contains("third") {
                s += 8;
            }
            if c.contains("push") {
                s += 4;
            }
            if c.contains("douyincdn") {
                s += 2;
            }
            s
        })
        .cloned()
}

/// Split an RTMP push URL into `(server, stream_key)`.
///
/// Handles both `/third/...` and `/thirdgame/...` forms, with a generic
/// last-segment fallback for anything else.
pub fn split_push_url(url: &str) -> Option<(String, String)> {
    // Primary: host + /third[...] + stream ?query
    let re = Regex::new(r"^(rtmps?://[^/]+)(/third\w*)/([^?]+)(\?.*)?$").ok()?;
    if let Some(c) = re.captures(url) {
        let server = format!("{}{}", &c[1], &c[2]);
        let stream = c.get(3).map(|m| m.as_str()).unwrap_or("");
        let query = c.get(4).map(|m| m.as_str()).unwrap_or("");
        return Some((server, format!("{stream}{query}")));
    }

    // Generic fallback: everything up to the last path segment is the server,
    // the last segment (+query) is the key.
    let rest = url.split_once("://").map(|(_, r)| r).unwrap_or(url);
    let (host_and_dir, key) = match rest.rsplit_once('/') {
        Some((dir, key)) => (dir, key),
        None => return None,
    };
    let scheme = url.split("://").next().unwrap_or("rtmp");
    let server = format!("{scheme}://{host_and_dir}");
    Some((server, key.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_third() {
        let (s, k) =
            split_push_url("rtmp://host/third/stream-1?txSecret=a&txTime=1").unwrap();
        assert_eq!(s, "rtmp://host/third");
        assert_eq!(k, "stream-1?txSecret=a&txTime=1");
    }

    #[test]
    fn splits_thirdgame() {
        let (s, k) = split_push_url(
            "rtmp://push-rtmp-l11.douyincdn.com/thirdgame/stream-4081?arch_hrchy=c1&sign=x",
        )
        .unwrap();
        assert_eq!(s, "rtmp://push-rtmp-l11.douyincdn.com/thirdgame");
        assert_eq!(k, "stream-4081?arch_hrchy=c1&sign=x");
    }

    #[test]
    fn splits_generic() {
        let (s, k) = split_push_url("rtmp://a.com/app/stream?k=v").unwrap();
        assert_eq!(s, "rtmp://a.com/app");
        assert_eq!(k, "stream?k=v");
    }

    #[test]
    fn picks_third_over_push() {
        let c = vec![
            "rtmp://h/other/stream-1?x=1".to_string(),
            "rtmp://h/thirdgame/stream-2?x=2".to_string(),
            "rtmp://push-h/stream-3?x=3".to_string(),
        ];
        let best = pick_best(&c).unwrap();
        assert!(best.contains("third"));
    }
}
