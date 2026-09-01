# Android Optimizer — Rust/Tauri backend skeleton

This is the **backend half** of the project we scoped out (Tauri + Rust).
It's fully modular and **100% synchronous** — no `tokio`, no threads, no
async fn anywhere. Tauri itself dispatches each `#[tauri::command]` on its
own worker thread, which is enough concurrency for an ADB-bound app like
this; you don't need to add any yourself.

## Layout

```
android_optimizer/
├── Cargo.toml
├── build.rs
├── tauri.conf.json
├── config/
│   └── rules.default.json      ← editable optimizer ruleset (see review point #5)
└── src/
    ├── main.rs                 ← entry point, just calls lib::run()
    ├── lib.rs                  ← module tree + Tauri builder/setup
    ├── models.rs                ← shared structs (Device, AppInfo, Recommendation, ...)
    ├── commands.rs               ← #[tauri::command] functions the frontend calls
    ├── adb/
    │   ├── mod.rs
    │   ├── connection.rs        ← single chokepoint for spawning `adb`
    │   ├── devices.rs           ← device discovery + dashboard info + root check
    │   ├── packages.rs          ← installed packages + Force Stop/Disable/etc.
    │   └── processes.rs         ← running apps, RAM (dumpsys meminfo), CPU (ps)
    └── optimizer/
        ├── mod.rs
        ├── rules.rs             ← config-driven ruleset (loads config/rules.default.json)
        └── analyzer.rs          ← turns AppInfo + RuleSet into Recommendations
```

## How the pieces connect

1. `commands::list_devices` → `adb::devices::list_devices` (no device selected yet).
2. `commands::connect_device(serial)` → creates an `AdbConnection`, stores it in
   `AppState`, returns `DeviceInfo` (includes root status — used to gate
   root-only features later, per review point #6).
3. `commands::get_app_list` → merges `pm list packages` + `dumpsys meminfo` +
   `ps -A` into one `Vec<AppInfo>`.
4. `commands::get_recommendations` → runs `get_app_list` through
   `optimizer::analyze`, which checks each app against `RuleSet` (loaded from
   `config/rules.default.json`, not hardcoded — review point #5) and returns
   a `Recommendation` with a human-readable `reason` for every app.
5. `commands::run_package_action` / `run_batch_package_action` → Force Stop,
   Disable, Clear Cache, etc.

## What's intentionally stubbed / left for you to extend

- **Frontend**: none included — this is the Rust side only, as requested.
  `tauri.conf.json`'s `frontendDist` is a placeholder; point it at your
  actual `src/` once you scaffold the TS/HTML side.
- **Logcat viewer, File Explorer, Performance History**: not yet built —
  these were Phase 3/4/5 in the roadmap. The existing `adb::connection`
  chokepoint and `AppState` pattern extend cleanly to them (e.g. a new
  `adb/logcat.rs`, `adb/files.rs`, `history/` module).
- **Wireless ADB pairing / multi-device**: `AdbConnection` is already
  serial-scoped (review point #2), so supporting multiple simultaneous
  devices later mainly means changing `AppState` to hold a
  `Mutex<HashMap<String, AdbConnection>>` instead of a single
  `Mutex<Option<AdbConnection>>> — no rewrite of `adb::*` needed.
- **High-frequency live polling**: each ADB call here spawns a fresh
  `adb` process (see the comment in `connection.rs`). Fine for polling
  every few seconds; if you later want sub-second updates, that's the one
  place to swap in a persistent `adb shell` session.

## Building

This skeleton can't be compiled in this sandbox (no network access to fetch
crates), but it's a standard Tauri v2 Rust project:

```bash
cd android_optimizer
cargo check     # once you have a Tauri frontend wired up via tauri.conf.json
```

You'll need the Tauri CLI and a frontend scaffold (`npm create tauri-app`)
for `tauri dev`/`tauri build` to fully work — this delivers the Rust side
described in our architecture doc.
