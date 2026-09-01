//! Package listing and package-level actions (Force Stop, Disable, Clear
//! Cache, etc). None of these currently require root - Android exposes all
//! of them to `adb shell pm` / `adb shell am` for the calling user/profile.

use crate::adb::connection::{AdbConnection, AdbResult};
use crate::models::{AppInfo, PackageAction};
use std::collections::HashSet;

/// Lists installed packages, split by system/user flag so the App Scanner
/// can filter without a second round-trip.
pub fn list_installed_packages(conn: &AdbConnection) -> AdbResult<Vec<AppInfo>> {
    let user_raw = conn.shell("pm list packages -3")?; // -3: third-party (user) apps
    let system_raw = conn.shell("pm list packages -s")?; // -s: system apps

    let user_packages: HashSet<String> = parse_package_list(&user_raw);
    let system_packages: HashSet<String> = parse_package_list(&system_raw);

    let mut apps = Vec::with_capacity(user_packages.len() + system_packages.len());

    for pkg in user_packages {
        apps.push(AppInfo {
            package_name: pkg,
            display_name: None,
            is_system_app: false,
            is_running: false,
            pid: None,
            rss_kb: None,
            cpu_percent: None,
            has_foreground_service: false,
        });
    }

    for pkg in system_packages {
        apps.push(AppInfo {
            package_name: pkg,
            display_name: None,
            is_system_app: true,
            is_running: false,
            pid: None,
            rss_kb: None,
            cpu_percent: None,
            has_foreground_service: false,
        });
    }

    Ok(apps)
}

fn parse_package_list(raw: &str) -> HashSet<String> {
    raw.lines()
        .filter_map(|line| line.strip_prefix("package:"))
        .map(|s| s.trim().to_string())
        .collect()
}

/// Cross-references a process list (from `dumpsys meminfo`) against the full
/// package list so `AppInfo::is_system_app` and `is_running` are set
/// correctly on every entry. Returns a single merged, deduplicated list.
pub fn annotate_running_state(
    installed: Vec<AppInfo>,
    running: &[AppInfo],
) -> Vec<AppInfo> {
    let running_set: HashSet<&str> = running.iter().map(|a| a.package_name.as_str()).collect();

    installed
        .into_iter()
        .map(|mut app| {
            if let Some(r) = running.iter().find(|r| r.package_name == app.package_name) {
                app.is_running = true;
                app.pid = r.pid;
                app.rss_kb = r.rss_kb;
                app.cpu_percent = r.cpu_percent;
            } else {
                app.is_running = running_set.contains(app.package_name.as_str());
            }
            app
        })
        .collect()
}

/// Executes a package action via `adb shell pm` / `adb shell am`.
/// Returns the raw shell output (usually "Success" or an error string from
/// the device itself) so the frontend can surface device-reported failures
/// (e.g. "DELETE_FAILED_INTERNAL_ERROR") rather than a generic Rust error.
pub fn run_action(
    conn: &AdbConnection,
    package_name: &str,
    action: PackageAction,
) -> AdbResult<String> {
    let cmd = match action {
        PackageAction::ForceStop => format!("am force-stop {package_name}"),
        PackageAction::Disable => format!("pm disable-user --user 0 {package_name}"),
        PackageAction::Enable => format!("pm enable {package_name}"),
        PackageAction::ClearCache => format!("pm trim-caches 1 {package_name}"),
        PackageAction::ClearData => format!("pm clear {package_name}"),
        PackageAction::Uninstall => format!("pm uninstall --user 0 {package_name}"),
    };

    conn.shell(&cmd)
}

/// Convenience for the "Batch Select" UI: runs the same action across many
/// packages and collects per-package results rather than failing fast, so
/// one bad package doesn't block the rest of the batch.
pub fn run_batch_action(
    conn: &AdbConnection,
    package_names: &[String],
    action: PackageAction,
) -> Vec<(String, AdbResult<String>)> {
    package_names
        .iter()
        .map(|pkg| (pkg.clone(), run_action(conn, pkg, action)))
        .collect()
}
