//! Shared data structures. Kept separate from logic so both `adb::*` and
//! `optimizer::*` can depend on them without circular imports, and so the
//! frontend (via serde_json) sees a stable contract.

use serde::{Deserialize, Serialize};

/// A connected Android device, as reported by `adb devices -l`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Device {
    pub serial: String,
    pub state: DeviceState,
    pub transport: ConnectionType,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DeviceState {
    Device,
    Offline,
    Unauthorized,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ConnectionType {
    Usb,
    Wifi,
    Unknown,
}

/// Static + slow-changing device info, used by the Dashboard.
/// Root status is checked once per connection and gates which actions
/// the rest of the app is allowed to expose.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceInfo {
    pub serial: String,
    pub manufacturer: String,
    pub model: String,
    pub android_version: String,
    pub api_level: String,
    pub security_patch: String,
    pub is_rooted: bool,
    pub battery_percent: Option<u8>,
    pub battery_charging: bool,
    pub uptime_seconds: Option<u64>,
}

/// A single installed app/process, merged from `pm list packages` and
/// `dumpsys meminfo` / `ps -A`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppInfo {
    pub package_name: String,
    pub display_name: Option<String>,
    pub is_system_app: bool,
    pub is_running: bool,
    pub pid: Option<u32>,
    pub rss_kb: Option<u64>,
    pub cpu_percent: Option<f32>,
    pub has_foreground_service: bool,
}

/// Lightweight live snapshot used by the Performance Monitor + History recorder.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PerformanceSnapshot {
    pub timestamp: chrono::DateTime<chrono::Utc>,
    pub cpu_percent: Option<f32>,
    pub ram_used_kb: Option<u64>,
    pub ram_total_kb: Option<u64>,
    pub storage_used_mb: Option<u64>,
    pub storage_total_mb: Option<u64>,
    pub battery_percent: Option<u8>,
}

/// Output of the rule-based optimizer for a single app.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Recommendation {
    pub package_name: String,
    pub action: RecommendedAction,
    pub reason: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RecommendedAction {
    SafeToForceStop,
    DoNotStop,
    NoActionNeeded,
}

/// Actions the Package Manager module can perform. Some require root;
/// `requires_root` lets the frontend grey these out based on `DeviceInfo::is_rooted`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PackageAction {
    ForceStop,
    Disable,
    Enable,
    ClearCache,
    ClearData,
    Uninstall,
}

impl PackageAction {
    pub fn requires_root(self) -> bool {
        false // none of the current actions need root; placeholder for future ones (e.g. freeze).
    }
}
