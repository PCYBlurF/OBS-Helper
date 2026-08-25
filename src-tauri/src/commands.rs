//! Tauri command handlers and shared application state.

use crate::proxy::{ProxyRunner, spawn_proxy};
use crate::{sys, Capture};
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter, State};
use tokio::net::TcpListener;

/// Shared Tauri-managed state.
pub struct AppState {
    pub runner: Mutex<Option<ProxyRunner>>,
    pub last_capture: Arc<Mutex<Option<Capture>>>,
    pub restore_on_exit: Mutex<bool>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            runner: Mutex::new(None),
            last_capture: Arc::new(Mutex::new(None)),
            restore_on_exit: Mutex::new(true),
        }
    }
}

#[tauri::command]
pub async fn start_proxy(
    app: AppHandle,
    state: State<'_, AppState>,
    host: String,
    port: u16,
) -> Result<String, String> {
    if state.runner.lock().unwrap().is_some() {
        // Binding to the same port would fail anyway; treat as already running.
        let _ = app.emit("log", "代理已在运行");
        return Ok("代理已在运行".into());
    }

    let ca = sys::ensure_ca()?;
    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .map_err(|e| format!("监听地址无效: {e}"))?;
    let listener = TcpListener::bind(addr)
        .await
        .map_err(|e| format!("端口 {port} 无法绑定（可能已占用）: {e}"))?;

    let runner = spawn_proxy(listener, ca, app.clone(), state.last_capture.clone()).await?;
    let bound = runner.addr;
    let _ = app.emit("log", format!("代理已启动，监听 {bound}（本机 {host}:{port}）"));
    *state.runner.lock().unwrap() = Some(runner);
    Ok(format!("代理已启动，监听 {bound}"))
}

#[tauri::command]
pub async fn stop_proxy(app: AppHandle, state: State<'_, AppState>) -> Result<String, String> {
    let runner = state.runner.lock().unwrap().take();
    match runner {
        Some(r) => {
            let addr = r.addr;
            r.stop().await;
            let _ = app.emit("log", format!("代理已停止（原监听 {addr}）"));
            Ok("代理已停止".into())
        }
        None => Ok("代理未在运行".into()),
    }
}

#[tauri::command]
pub fn install_cert(app: AppHandle) -> Result<String, String> {
    let msg = sys::install_ca_cert()?;
    let _ = app.emit("log", msg.clone());
    Ok(msg)
}

#[tauri::command]
pub fn set_proxy(app: AppHandle, host: String, port: u16) -> Result<String, String> {
    let msg = sys::set_system_proxy(&host, port)?;
    let _ = app.emit("log", msg.clone());
    Ok(msg)
}

#[tauri::command]
pub fn restore_proxy(app: AppHandle) -> Result<String, String> {
    let msg = sys::restore_system_proxy()?;
    let _ = app.emit("log", msg.clone());
    Ok(msg)
}

#[tauri::command]
pub fn kill_process(name: String) -> Result<String, String> {
    sys::kill_process(&name)
}

#[tauri::command]
pub fn is_admin() -> bool {
    sys::is_admin()
}

#[tauri::command]
pub fn set_restore_on_exit(state: State<'_, AppState>, enabled: bool) -> Result<(), String> {
    *state.restore_on_exit.lock().unwrap() = enabled;
    Ok(())
}

#[tauri::command]
pub fn copy_text(text: String) -> Result<String, String> {
    let mut cb = arboard::Clipboard::new().map_err(|e| format!("打开剪贴板失败: {e}"))?;
    cb.set_text(text).map_err(|e| format!("写入剪贴板失败: {e}"))?;
    Ok("已复制到剪贴板".into())
}

#[tauri::command]
pub fn get_capture(state: State<'_, AppState>) -> Option<Capture> {
    state.last_capture.lock().unwrap().clone()
}
