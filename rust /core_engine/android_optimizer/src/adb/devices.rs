//! Device discovery (`adb devices -l`) and the per-device info needed for the
//! Dashboard. Root detection lives here because it's checked once per
//! connection and gates feature availability everywhere else in the app.

use crate::adb::connection::{AdbConnection, AdbResult};
use crate::models::{ConnectionType, Device, DeviceInfo, DeviceState};

/// Lists all devices currently visible to the local adb server.
/// Does not require a serial since no specific device is targeted yet.
pub fn list_devices(adb_path: &str) -> AdbResult<Vec<Device>> {
    let raw = AdbConnection::run_untargeted(adb_path, &["devices", "-l"])?;
    Ok(parse_devices(&raw))
}

fn parse_devices(raw: &str) -> Vec<Device> {
    raw.lines()
        .skip(1) // header line: "List of devices attached"
        .filter(|line| !line.trim().is_empty())
        .filter_map(|line| {
            let mut parts = line.split_whitespace();
            let serial = parts.next()?.to_string();
            let state_token = parts.next()?;

            let state = match state_token {
                "device" => DeviceState::Device,
                "offline" => DeviceState::Offline,
                "unauthorized" => DeviceState::Unauthorized,
                _ => DeviceState::Unknown,
            };

            // Wireless ADB serials look like "192.168.1.42:5555"; USB serials don't contain ':'.
            let transport = if serial.contains(':') {
                ConnectionType::Wifi
            } else {
                ConnectionType::Usb
            };

            Some(Device {
                serial,
                state,
                transport,
            })
        })
        .collect()
}

/// Pulls the static + slow-changing fields shown on the Dashboard.
/// Each `getprop`/shell call is independent and best-effort: a failure on
/// one property should not block the rest of the dashboard from rendering,
/// so missing values are surfaced as `"unknown"` / `None` rather than erroring.
pub fn get_device_info(conn: &AdbConnection) -> AdbResult<DeviceInfo> {
    let manufacturer = conn
        .shell("getprop ro.product.manufacturer")
        .unwrap_or_else(|_| "unknown".to_string());
    let model = conn
        .shell("getprop ro.product.model")
        .unwrap_or_else(|_| "unknown".to_string());
    let android_version = conn
        .shell("getprop ro.build.version.release")
        .unwrap_or_else(|_| "unknown".to_string());
    let api_level = conn
        .shell("getprop ro.build.version.sdk")
        .unwrap_or_else(|_| "unknown".to_string());
    let security_patch = conn
        .shell("getprop ro.build.version.security_patch")
        .unwrap_or_else(|_| "unknown".to_string());

    let battery_percent = conn
        .shell("dumpsys battery | grep level")
        .ok()
        .and_then(|s| parse_battery_field(&s, "level"));

    let battery_charging = conn
        .shell("dumpsys battery | grep status")
        .ok()
        .map(|s| s.contains("2")) // BatteryManager.BATTERY_STATUS_CHARGING == 2
        .unwrap_or(false);

    let uptime_seconds = conn
        .shell("cat /proc/uptime")
        .ok()
        .and_then(|s| s.split('.').next()?.parse::<u64>().ok());

    Ok(DeviceInfo {
        serial: conn.serial().to_string(),
        manufacturer,
        model,
        android_version,
        api_level,
        security_patch,
        is_rooted: check_root(conn),
        battery_percent,
        battery_charging,
        uptime_seconds,
    })
}

fn parse_battery_field(line: &str, field: &str) -> Option<u8> {
    // Lines look like: "  level: 81"
    line.split(':')
        .nth(1)
        .map(|v| v.trim())
        .and_then(|v| v.parse::<u8>().ok())
        .filter(|_| line.to_lowercase().contains(field))
}

/// Best-effort root detection. Tries `su -c id` first (most reliable signal);
/// falls back to checking for a `su` binary on common paths. Neither check is
/// foolproof (some ROMs hide root from unapproved callers), so this is a
/// "probably rooted" signal used purely to gate optional UI, never to decide
/// whether a destructive action is safe.
fn check_root(conn: &AdbConnection) -> bool {
    if let Ok(out) = conn.shell("su -c id") {
        if out.contains("uid=0") {
            return true;
        }
    }

    matches!(
        conn.shell("which su"),
        Ok(ref path) if !path.is_empty()
    )
}
