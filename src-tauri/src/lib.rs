mod commands;
mod proxy;
mod sys;

pub use commands::AppState;
use tauri::{Manager, RunEvent};

#[derive(Debug, Clone, serde::Serialize)]
pub struct Capture {
    pub server: String,
    pub stream_key: String,
    pub push_url: String,
    pub source: String,
    pub time: u64,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            commands::start_proxy,
            commands::stop_proxy,
            commands::install_cert,
            commands::set_proxy,
            commands::restore_proxy,
            commands::kill_process,
            commands::is_admin,
            commands::set_restore_on_exit,
            commands::copy_text,
            commands::get_capture,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            let state = app_handle.state::<AppState>();
            let restore = *state.restore_on_exit.lock().unwrap();
            if restore {
                let _ = sys::restore_system_proxy();
            }
        }
    });
}
