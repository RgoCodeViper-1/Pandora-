//! Tauri command surface. Every function here is a plain synchronous
//! function (no `async fn`) - Tauri dispatches each call on its own
//! command-handling thread, so this stays correct without us writing any
//! explicit concurrency code.
//!
//! `AppState` holds the currently-selected device connection. A `Mutex` is
//! used only because Tauri's `State<T>` requires `T: Send + Sync` for
//! sharing across its internal thread pool - this is bookkeeping required
//! by the framework, not application-level concurrency logic.

use std::path::PathBuf;
use std::sync::Mutex;

use tauri::State;

use crate::adb::{connection::AdbConnection, devices, packages, processes};
use crate::models::{AppInfo, Device, DeviceInfo, PackageAction, Recommendation};
use crate::optimizer::{self, RuleSet};

pub struct AppState {
    pub adb_path: String,
    pub active_connection: Mutex<Option<AdbConnection>>,
    pub rules_path: PathBuf,
}

impl AppState {
    pub fn new(adb_path: impl Into<String>, rules_path: PathBuf) -> Self {
        Self {
            adb_path: adb_path.into(),
            active_connection: Mutex::new(None),
            rules_path,
        }
    }
}

/// Maps any error type into the `String` Tauri expects on the Err side of a
/// command result (Tauri serializes command errors to the frontend as-is).
fn to_cmd_err<E: std::fmt::Display>(err: E) -> String {
    err.to_string()
}

#[tauri::command]
pub fn list_devices(state: State<AppState>) -> Result<Vec<Device>, String> {
    devices::list_devices(&state.adb_path).map_err(to_cmd_err)
}

/// Selects a device by serial for all subsequent commands. Must be called
/// before any device-scoped command (dashboard, app list, actions, etc.).
#[tauri::command]
pub fn connect_device(state: State<AppState>, serial: String) -> Result<DeviceInfo, String> {
    let conn = AdbConnection::new(serial).with_adb_path(state.adb_path.clone());
    let info = devices::get_device_info(&conn).map_err(to_cmd_err)?;

    *state.active_connection.lock().unwrap() = Some(conn);
    Ok(info)
}

#[tauri::command]
pub fn get_dashboard_info(state: State<AppState>) -> Result<DeviceInfo, String> {
    with_active_connection(&state, |conn| {
        devices::get_device_info(conn).map_err(to_cmd_err)
    })
}

/// Returns the merged app list (installed + running + cpu), ready for the
/// App Scanner view. This intentionally makes three ADB round-trips
/// (`pm list packages` x2, `dumpsys meminfo`, `ps -A`) rather than one giant
/// call, so the frontend can choose to poll the cheap `ps` call more often
/// than the others if it wants finer-grained CPU updates later.
#[tauri::command]
pub fn get_app_list(state: State<AppState>) -> Result<Vec<AppInfo>, String> {
    with_active_connection(&state, |conn| {
        let installed = packages::list_installed_packages(conn).map_err(to_cmd_err)?;
        let mut running = processes::list_running_apps(conn).map_err(to_cmd_err)?;

        let cpu = processes::cpu_snapshot(conn).map_err(to_cmd_err)?;
        processes::merge_cpu_usage(&mut running, &cpu);

        Ok(packages::annotate_running_state(installed, &running))
    })
}

#[tauri::command]
pub fn get_recommendations(state: State<AppState>) -> Result<Vec<Recommendation>, String> {
    let apps = get_app_list(state.clone())?;
    let rules = RuleSet::load_from_file(&state.rules_path);
    Ok(optimizer::analyze(&apps, &rules))
}

#[tauri::command]
pub fn run_package_action(
    state: State<AppState>,
    package_name: String,
    action: PackageAction,
) -> Result<String, String> {
    with_active_connection(&state, |conn| {
        packages::run_action(conn, &package_name, action).map_err(to_cmd_err)
    })
}

#[tauri::command]
pub fn run_batch_package_action(
    state: State<AppState>,
    package_names: Vec<String>,
    action: PackageAction,
) -> Result<Vec<(String, Result<String, String>)>, String> {
    with_active_connection(&state, |conn| {
        Ok(packages::run_batch_action(conn, &package_names, action)
            .into_iter()
            .map(|(pkg, res)| (pkg, res.map_err(to_cmd_err)))
            .collect())
    })
}

/// Shared guard: every device-scoped command needs an active connection.
/// Centralizing the "no device selected" error here keeps that message
/// consistent and avoids repeating the lock/unwrap dance in every command.
fn with_active_connection<T>(
    state: &State<AppState>,
    f: impl FnOnce(&AdbConnection) -> Result<T, String>,
) -> Result<T, String> {
    let guard = state.active_connection.lock().unwrap();
    match guard.as_ref() {
        Some(conn) => f(conn),
        None => Err("No device connected. Call connect_device first.".to_string()),
    }
}
