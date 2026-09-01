//! A loadable ruleset for the optimizer, instead of a hardcoded denylist.
//! OEM-critical-service names vary a lot between Pixel/Samsung/Xiaomi/etc,
//! so this is designed to be shipped as a JSON file (see
//! `config/rules.default.json`) that can be updated without recompiling.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuleSet {
    /// Packages (or prefixes, see `matches`) that must never be recommended
    /// for force-stop, regardless of RAM/CPU usage.
    pub protected: Vec<ProtectedEntry>,
    /// RAM threshold (KB) above which an otherwise-unprotected app is
    /// flagged as safe to close.
    pub safe_to_close_rss_threshold_kb: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProtectedEntry {
    /// Either an exact package name ("com.android.systemui") or a prefix
    /// ending in "*" ("com.samsung.*") for OEM service families.
    pub pattern: String,
    pub reason: String,
}

impl ProtectedEntry {
    fn matches(&self, package_name: &str) -> bool {
        match self.pattern.strip_suffix('*') {
            Some(prefix) => package_name.starts_with(prefix),
            None => package_name == self.pattern,
        }
    }
}

impl RuleSet {
    /// Sensible defaults covering AOSP + Google Play Services. OEM-specific
    /// entries (Samsung/Xiaomi/Oppo/etc) should be added via the JSON config
    /// rather than here, since this list will go stale otherwise.
    pub fn default_ruleset() -> Self {
        let protected = vec![
            ("android", "Core Android system package"),
            ("com.android.systemui", "System UI - required for the device shell"),
            ("com.android.phone", "Handles cellular/telephony"),
            ("com.android.bluetooth", "Bluetooth stack"),
            ("com.google.android.gms", "Google Play Services - critical Android service"),
            ("com.google.android.gsf", "Google Services Framework"),
            ("system_server", "Core Android system server process"),
        ]
        .into_iter()
        .map(|(pattern, reason)| ProtectedEntry {
            pattern: pattern.to_string(),
            reason: reason.to_string(),
        })
        .collect();

        Self {
            protected,
            safe_to_close_rss_threshold_kb: 150_000, // ~150 MB
        }
    }

    /// Loads a ruleset from a JSON file on disk, falling back to
    /// `default_ruleset()` if the file is missing or malformed. Optimization
    /// recommendations should never hard-fail the app just because a config
    /// file got corrupted.
    pub fn load_from_file(path: &std::path::Path) -> Self {
        std::fs::read_to_string(path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(Self::default_ruleset)
    }

    pub fn protection_reason(&self, package_name: &str) -> Option<&str> {
        self.protected
            .iter()
            .find(|entry| entry.matches(package_name))
            .map(|entry| entry.reason.as_str())
    }
}
