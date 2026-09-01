//! Single chokepoint for talking to the `adb` binary. Every other module in
//! `adb::*` goes through `AdbConnection::run` rather than calling
//! `std::process::Command` directly, so:
//!   1. We have one place to swap in a persistent `adb shell` session later
//!      (today each call spawns a fresh process - fine for low-frequency
//!      polling, but should be revisited if poll intervals drop below ~1s).
//!   2. Error handling and serial-targeting stay consistent everywhere.
//!
//! Deliberately synchronous: no tokio, no threads. Tauri commands that call
//! into this run on Tauri's own command-dispatch thread pool, which is enough
//! for an ADB-bound workload like this.

use std::process::Command;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum AdbError {
    #[error("failed to launch adb: {0}")]
    Spawn(#[from] std::io::Error),
    #[error("adb exited with non-zero status: {stderr}")]
    NonZeroExit { stderr: String },
    #[error("adb output was not valid UTF-8")]
    InvalidUtf8,
}

pub type AdbResult<T> = Result<T, AdbError>;

/// Represents a connection scoped to one device (by serial). Holding this
/// instead of passing the serial around everywhere also makes the
/// multi-device case (`Vec<AdbConnection>`) a non-refactor later.
#[derive(Debug, Clone)]
pub struct AdbConnection {
    serial: String,
    adb_path: String,
}

impl AdbConnection {
    pub fn new(serial: impl Into<String>) -> Self {
        Self {
            serial: serial.into(),
            adb_path: "adb".to_string(), // override via `with_adb_path` if adb isn't on PATH
        }
    }

    pub fn with_adb_path(mut self, path: impl Into<String>) -> Self {
        self.adb_path = path.into();
        self
    }

    pub fn serial(&self) -> &str {
        &self.serial
    }

    /// Runs `adb -s <serial> <args...>` and returns trimmed stdout.
    pub fn run(&self, args: &[&str]) -> AdbResult<String> {
        let output = Command::new(&self.adb_path)
            .arg("-s")
            .arg(&self.serial)
            .args(args)
            .output()?;

        if !output.status.success() {
            return Err(AdbError::NonZeroExit {
                stderr: String::from_utf8_lossy(&output.stderr).trim().to_string(),
            });
        }

        String::from_utf8(output.stdout)
            .map(|s| s.trim().to_string())
            .map_err(|_| AdbError::InvalidUtf8)
    }

    /// Convenience wrapper for `adb -s <serial> shell <cmd>`.
    pub fn shell(&self, cmd: &str) -> AdbResult<String> {
        self.run(&["shell", cmd])
    }

    /// Runs a bare `adb <args...>` with no device targeting, e.g. `adb devices -l`.
    /// Use the free function `run_untargeted` below instead if you don't have
    /// a serial yet (e.g. during device discovery).
    pub fn run_untargeted(adb_path: &str, args: &[&str]) -> AdbResult<String> {
        let output = Command::new(adb_path).args(args).output()?;

        if !output.status.success() {
            return Err(AdbError::NonZeroExit {
                stderr: String::from_utf8_lossy(&output.stderr).trim().to_string(),
            });
        }

        String::from_utf8(output.stdout)
            .map(|s| s.trim().to_string())
            .map_err(|_| AdbError::InvalidUtf8)
    }
}
