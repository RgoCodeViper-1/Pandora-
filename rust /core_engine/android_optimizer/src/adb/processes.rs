//! Running-process / RAM info for the App Scanner and Performance Monitor.
//! Uses `dumpsys meminfo` (richer, slower) for app RAM and `ps -A` (cheap)
//! when only PIDs/CPU are needed - keep these calls separate so callers can
//! poll them at different intervals (see Optimization #1 from the review).

use crate::adb::connection::{AdbConnection, AdbResult};
use crate::models::AppInfo;
use std::collections::HashMap;

/// Lists currently-running app processes with their RSS (resident memory),
/// parsed from `dumpsys meminfo`. This call is relatively expensive on
/// device (commonly 200-500ms) - callers should not poll it faster than
/// every few seconds.
pub fn list_running_apps(conn: &AdbConnection) -> AdbResult<Vec<AppInfo>> {
    let raw = conn.shell("dumpsys meminfo")?;
    Ok(parse_meminfo(&raw))
}

/// "## Proc memory usage" rows in `dumpsys meminfo` look roughly like:
///   "  123,456K: com.android.chrome (pid 4321 / activities)"
/// We deliberately parse only this section; the full output is much larger
/// and most of it isn't needed for the App Scanner view.
fn parse_meminfo(raw: &str) -> Vec<AppInfo> {
    let mut apps = Vec::new();

    for line in raw.lines() {
        let trimmed = line.trim();
        if !trimmed.ends_with(')') || !trimmed.contains("(pid ") {
            continue;
        }

        let Some((mem_part, rest)) = trimmed.split_once(':') else {
            continue;
        };
        let rss_kb = mem_part.trim().replace(',', "").parse::<u64>().ok();

        let Some(pid_start) = rest.find("(pid ") else {
            continue;
        };
        let package_name = rest[..pid_start].trim().to_string();
        if package_name.is_empty() {
            continue;
        }

        let pid = rest[pid_start + 5..]
            .split(|c: char| !c.is_ascii_digit())
            .next()
            .and_then(|s| s.parse::<u32>().ok());

        apps.push(AppInfo {
            package_name,
            display_name: None,
            is_system_app: false, // filled in later by packages::annotate_system_apps
            is_running: true,
            pid,
            rss_kb,
            cpu_percent: None,
            has_foreground_service: false,
        });
    }

    apps
}

/// Cheap CPU-percent snapshot via `ps -A -o NAME,PID,%CPU`. Returns a map of
/// process name -> cpu percent so callers can merge it onto the richer
/// `AppInfo` list from `list_running_apps` without a second `dumpsys meminfo`
/// call. Zero-cpu entries are dropped to keep the map small.
pub fn cpu_snapshot(conn: &AdbConnection) -> AdbResult<HashMap<String, f32>> {
    let raw = conn.shell("ps -A -o NAME,PID,%CPU")?;
    let mut map = HashMap::new();

    for line in raw.lines().skip(1) {
        let mut cols = line.split_whitespace();
        let Some(name) = cols.next() else { continue };
        let _pid = cols.next();
        let Some(cpu_str) = cols.next() else { continue };

        if let Ok(cpu) = cpu_str.parse::<f32>() {
            if cpu > 0.0 {
                map.insert(name.to_string(), cpu);
            }
        }
    }

    Ok(map)
}

/// Merges a CPU map (from `cpu_snapshot`) onto an `AppInfo` list in place.
pub fn merge_cpu_usage(apps: &mut [AppInfo], cpu: &HashMap<String, f32>) {
    for app in apps.iter_mut() {
        if let Some(&pct) = cpu.get(app.package_name.as_str()) {
            app.cpu_percent = Some(pct);
        }
    }
}
