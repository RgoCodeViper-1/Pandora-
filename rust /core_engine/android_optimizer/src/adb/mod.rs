//! Everything related to talking to a device over ADB.
//! `connection` is the only module that spawns processes directly - every
//! other module here goes through `AdbConnection`.

pub mod connection;
pub mod devices;
pub mod packages;
pub mod processes;

pub use connection::{AdbConnection, AdbError, AdbResult};
