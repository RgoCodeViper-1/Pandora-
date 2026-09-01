//! Crate root. Declares the module tree and exposes `run()`, which
//! `main.rs` calls into. Keeping this split (lib.rs + main.rs) is the
//! standard Tauri pattern and also lets the backend logic be unit-tested
//! without going through a binary entry point.

pub mod adb;
pub mod commands;
pub mod models;
pub mod optimizer;

use std::path::PathBuf;

use commands::AppState;
use tauri::Manager;

/// Resolves the path to the rules config, preferring a user-editable copy
/// next to the executable (so it survives app updates) and falling back to
/// the bundled default. `RuleSet::load_from_file` already falls back to
/// hardcoded defaults if neither file exists or parses, so this never
/// blocks startup.
fn resolve_rules_path(app_handle: &tauri::AppHandle) -> PathBuf {
    if let Ok(dir) = app_handle.path().app_config_dir() {
        let user_path = dir.join("rules.json");
        if user_path.exists() {
            return user_path;
        }
    }

    // Bundled fallback, shipped under `config/rules.default.json`.
    PathBuf::from("config/rules.default.json")
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let rules_path = resolve_rules_path(&app.handle());
            app.manage(AppState::new("adb", rules_path));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::list_devices,
            commands::connect_device,
            commands::get_dashboard_info,
            commands::get_app_list,
            commands::get_recommendations,
            commands::run_package_action,
            commands::run_batch_package_action,
        ])
        .run(tauri::generate_context!())
        .expect("error while running android_optimizer");
}
