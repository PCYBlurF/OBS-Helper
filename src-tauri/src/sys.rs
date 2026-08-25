//! Windows system integration: CA certificate generation/install, system proxy
//! set/restore, process kill and privilege detection.

#![allow(dead_code)]

use hudsucker::certificate_authority::RcgenAuthority;
use hudsucker::rcgen::{
    BasicConstraints, CertificateParams, DnType, DistinguishedName, IsCa, Issuer, KeyPair,
    KeyUsagePurpose, SerialNumber,
};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};
use winreg::enums::*;
use winreg::RegKey;

const INTERNET_SETTINGS_KEY: &str =
    r"Software\Microsoft\Windows\CurrentVersion\Internet Settings";

/// Directory that stores CA cert/key and the saved system-proxy state.
pub fn data_dir() -> PathBuf {
    let d = dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".obs_helper");
    let _ = std::fs::create_dir_all(&d);
    d
}

pub fn ca_cert_path() -> PathBuf {
    data_dir().join("ca.cer")
}
pub fn ca_key_path() -> PathBuf {
    data_dir().join("ca.key")
}
pub fn ca_pem_path() -> PathBuf {
    data_dir().join("ca.pem")
}
pub fn proxy_state_path() -> PathBuf {
    data_dir().join("_proxy_state.json")
}

/// Whether the current process is running with administrator privileges.
pub fn is_admin() -> bool {
    #[cfg(windows)]
    {
        use windows_sys::Win32::UI::Shell::IsUserAnAdmin;
        unsafe { IsUserAnAdmin() != 0 }
    }
    #[cfg(not(windows))]
    {
        true
    }
}

/// Elevate (UAC) a command and return immediately, without waiting.
fn elevate(file: &str, params: &str) {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::UI::Shell::ShellExecuteW;

        fn wid(s: &str) -> Vec<u16> {
            std::ffi::OsStr::new(s).encode_wide().chain(Some(0)).collect()
        }
        let op = wid("runas");
        let file_w = wid(file);
        let params_w = wid(params);
        unsafe {
            // SW_SHOWNORMAL = 1
            let _ = ShellExecuteW(
                std::ptr::null_mut(),
                op.as_ptr(),
                file_w.as_ptr(),
                params_w.as_ptr(),
                std::ptr::null(),
                1,
            );
        }
    }
    #[cfg(not(windows))]
    {
        let _ = (file, params);
    }
}

fn rustls_provider() -> hudsucker::rustls::crypto::CryptoProvider {
    use hudsucker::rustls::crypto::aws_lc_rs;
    aws_lc_rs::default_provider()
}

/// Ensure the per-app CA cert/key exist, returning an RcgenAuthority ready for
/// leaf-cert signing. Generates + persists the CA on first use.
pub fn ensure_ca() -> Result<RcgenAuthority, String> {
    let cert_path = ca_cert_path();
    let key_path = ca_key_path();
    let pem_path = ca_pem_path();

    if cert_path.exists() && key_path.exists() {
        let ca_pem =
            std::fs::read_to_string(&pem_path).map_err(|e| format!("读取 CA 证书失败: {e}"))?;
        let key_pem =
            std::fs::read_to_string(&key_path).map_err(|e| format!("读取 CA 私钥失败: {e}"))?;
        let key_pair = KeyPair::from_pem(&key_pem).map_err(|e| format!("解析 CA 私钥失败: {e}"))?;
        let issuer = Issuer::from_ca_cert_pem(&ca_pem, key_pair)
            .map_err(|e| format!("解析 CA 证书失败: {e}"))?;
        return Ok(RcgenAuthority::new(issuer, 1_000, rustls_provider()));
    }

    let mut params = CertificateParams::new(Vec::<String>::new())
        .map_err(|e| format!("生成证书参数失败: {e}"))?;
    let mut dn = DistinguishedName::new();
    dn.push(DnType::CommonName, "OBS权限助手 MITM Proxy CA");
    params.distinguished_name = dn;
    params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(1);
    params.serial_number = Some(SerialNumber::from(now));
    // rcgen's default validity (10 years) is plenty for a local MITM CA.

    let key_pair = KeyPair::generate().map_err(|e| format!("生成 CA 私钥失败: {e}"))?;
    let cert = params
        .self_signed(&key_pair)
        .map_err(|e| format!("生成 CA 证书失败: {e}"))?;

    std::fs::write(&pem_path, cert.pem()).map_err(|e| format!("写入 CA 证书失败: {e}"))?;
    std::fs::write(&key_path, key_pair.serialize_pem())
        .map_err(|e| format!("写入 CA 私钥失败: {e}"))?;
    std::fs::write(&cert_path, cert.der().to_vec())
        .map_err(|e| format!("写入 CA DER 失败: {e}"))?;

    let issuer = Issuer::from_ca_cert_pem(&cert.pem(), key_pair)
        .map_err(|e| format!("构建 CA Issuer 失败: {e}"))?;
    Ok(RcgenAuthority::new(issuer, 1_000, rustls_provider()))
}

/// Install the per-app CA cert into the Windows root trust store. Elevates if
/// not already an administrator.
pub fn install_ca_cert() -> Result<String, String> {
    let path = ca_cert_path();
    if !path.exists() {
        return Err("未找到 CA 证书，请先点击「① 启动代理」生成证书".into());
    }
    let path_str = path.to_string_lossy().into_owned();

    if is_admin() {
        let out = Command::new("certutil")
            .args(["-addstore", "-f", "Root", &path_str])
            .output()
            .map_err(|e| format!("执行 certutil 失败: {e}"))?;
        let code = out.status.code().unwrap_or(-1);
        let stdout = String::from_utf8_lossy(&out.stdout).to_string();
        let stderr = String::from_utf8_lossy(&out.stderr).to_string();
        if code == 0 {
            Ok("CA 证书已成功安装到系统根证书库".into())
        } else {
            Err(format!("certutil 返回 {code}: {stdout}{stderr}"))
        }
    } else {
        let params = format!("/c certutil -addstore -f Root \"{path_str}\"");
        elevate("cmd.exe", &params);
        Ok("已请求 UAC 提权安装 CA 证书，请在弹出的窗口中点击“是”".into())
    }
}

/// Saved HKCU Internet Settings, so they can be restored later.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ProxyState {
    #[serde(default)]
    pub proxy_enable: Option<u32>,
    #[serde(default)]
    pub proxy_server: Option<String>,
    #[serde(default)]
    pub proxy_override: Option<String>,
    #[serde(default)]
    pub auto_config_url: Option<String>,
}

fn open_settings_key() -> Result<RegKey, String> {
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    // Internet Settings must be opened read+write; winreg's `open_subkey`
    // defaults to KEY_READ only, which would fail writes with access denied.
    hkcu.open_subkey_with_flags(INTERNET_SETTINGS_KEY, KEY_READ | KEY_WRITE)
        .map_err(|e| format!("打开系统代理注册表失败: {e}"))
}

/// Set HKCU Internet Settings to route everything through our local proxy.
pub fn set_system_proxy(host: &str, port: u16) -> Result<String, String> {
    let proxy = format!("{host}:{port}");
    let key = open_settings_key()?;

    let old = ProxyState {
        proxy_enable: key.get_value::<u32, _>("ProxyEnable").ok(),
        proxy_server: key.get_value::<String, _>("ProxyServer").ok(),
        proxy_override: key.get_value::<String, _>("ProxyOverride").ok(),
        auto_config_url: key.get_value::<String, _>("AutoConfigURL").ok(),
    };
    std::fs::write(
        proxy_state_path(),
        serde_json::to_string_pretty(&old).map_err(|e| format!("保存代理状态失败: {e}"))?,
    )
    .map_err(|e| format!("写入代理状态文件失败: {e}"))?;

    key.set_value("ProxyEnable", &1u32)
        .map_err(|e| format!("设置 ProxyEnable 失败: {e}"))?;
    key.set_value("ProxyServer", &proxy)
        .map_err(|e| format!("设置 ProxyServer 失败: {e}"))?;
    key.set_value("ProxyOverride", &"<local>;localhost;127.*;192.168.*;10.*")
        .map_err(|e| format!("设置 ProxyOverride 失败: {e}"))?;
    key.set_value("AutoConfigURL", &"")
        .map_err(|e| format!("设置 AutoConfigURL 失败: {e}"))?;

    refresh_proxy();
    Ok(format!("已设置系统代理为 {proxy}，正在等待应用生效"))
}

/// Restore HKCU Internet Settings from the saved state, if any.
pub fn restore_system_proxy() -> Result<String, String> {
    let state_path = proxy_state_path();
    if !state_path.exists() {
        return Ok("未设置过系统代理，无需还原".into());
    }
    let raw = std::fs::read_to_string(&state_path).map_err(|e| format!("读取代理状态失败: {e}"))?;
    let old: ProxyState =
        serde_json::from_str(&raw).map_err(|e| format!("解析代理状态失败: {e}"))?;

    let key = open_settings_key()?;
    set_or_delete_str(&key, "ProxyServer", old.proxy_server.as_deref());
    set_or_delete_str(&key, "ProxyOverride", old.proxy_override.as_deref());
    set_or_delete_str(&key, "AutoConfigURL", old.auto_config_url.as_deref());
    match old.proxy_enable {
        Some(v) => {
            let _ = key.set_value("ProxyEnable", &v);
        }
        None => {
            let _ = key.delete_value("ProxyEnable");
        }
    }

    refresh_proxy();
    let _ = std::fs::remove_file(&state_path);
    Ok("已还原系统代理".into())
}

fn set_or_delete_str(key: &RegKey, name: &str, value: Option<&str>) {
    match value {
        Some(v) => {
            let _ = key.set_value(name, &v);
        }
        None => {
            let _ = key.delete_value(name);
        }
    }
}

/// Ask Windows to re-read the proxy settings live.
pub fn refresh_proxy() {
    #[cfg(windows)]
    {
        use windows_sys::Win32::Networking::WinInet::{
            InternetSetOptionW, INTERNET_OPTION_REFRESH, INTERNET_OPTION_SETTINGS_CHANGED,
        };
        unsafe {
            InternetSetOptionW(
                std::ptr::null_mut(),
                INTERNET_OPTION_SETTINGS_CHANGED,
                std::ptr::null_mut(),
                0,
            );
            InternetSetOptionW(
                std::ptr::null_mut(),
                INTERNET_OPTION_REFRESH,
                std::ptr::null_mut(),
                0,
            );
        }
    }
}

/// Force-kill a process by image name; auto-elevates on access denied.
pub fn kill_process(name: &str) -> Result<String, String> {
    let out = Command::new("taskkill")
        .args(["/F", "/IM", name])
        .output()
        .map_err(|e| format!("执行 taskkill 失败: {e}"))?;
    let code = out.status.code().unwrap_or(-1);
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    let low = format!("{stdout}{stderr}").to_lowercase();
    let denied = stdout.contains("拒绝访问")
        || stderr.contains("拒绝访问")
        || low.contains("access is denied")
        || low.contains("denied")
        || stdout.contains("拒绝");

    if denied && !is_admin() {
        let params = format!("/c taskkill /F /IM \"{name}\"");
        elevate("cmd.exe", &params);
        return Ok(format!("进程「{name}」权限不足，已请求 UAC 提权终止，请在弹出的窗口确认"));
    }

    if code == 0 || low.contains("success") || stdout.contains("成功") {
        Ok(format!("已结束进程：{name}"))
    } else if code == 128 || low.contains("not found") || stdout.contains("没有找到进程") {
        Ok(format!("进程「{name}」未在运行"))
    } else {
        Err(format!("结束进程失败(code {code}): {stdout}{stderr}"))
    }
}
