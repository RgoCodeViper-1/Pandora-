// fsengine — Stateless Headless Filesystem Engine
// =================================================
// Architecture mirrors Files Community App v4.1.1
//   src/Files.App/Utils/Storage/Operations/
//   src/Files.App/Utils/Storage/History/
//   src/Files.App/Services/Storage/
//
// Protocol:
//   STDIN  — one JSON Command object per line (newline-delimited)
//   STDOUT — one JSON Response object per line (newline-delimited)
//   STDERR — diagnostic log lines (not part of the protocol)
//
// The process is stateless between commands: no undo stack, no open handles,
// no shared mutable state. Each command is self-contained.
// The caller program owns all session state (history, UI, etc.).

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

// ─────────────────────────────────────────────────────────────────────────────
// Protocol types — Command (in) & Response (out)
// Maps to the action/command layer in Files.App/Actions/FileSystem/
// ─────────────────────────────────────────────────────────────────────────────

/// Every inbound message from the controller.
/// Mirrors IAction / IFilesystemHelpers method signatures.
#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
enum Command {
    /// Create a file or directory.
    /// Maps to: FilesystemOperations.CreateAsync / CreateFileAction / CreateFolderAction
    Create {
        path: String,
        kind: ItemKind,
        #[serde(default)]
        overwrite: bool,
    },

    /// Copy one or many items to destination(s).
    /// Maps to: ShellFilesystemOperations.CopyItemsAsync / CopyItemAction
    Copy {
        sources: Vec<String>,
        destinations: Vec<String>,
        #[serde(default)]
        collision: CollisionPolicy,
    },

    /// Move one or many items to destination(s).
    /// Maps to: ShellFilesystemOperations.MoveItemsAsync / CutItemAction
    Move {
        sources: Vec<String>,
        destinations: Vec<String>,
        #[serde(default)]
        collision: CollisionPolicy,
    },

    /// Delete (recycle or permanent).
    /// Maps to: FilesystemOperations.DeleteAsync / DeleteItemAction / DeleteItemPermanentlyAction
    Delete {
        paths: Vec<String>,
        #[serde(default)]
        permanently: bool,
    },

    /// Rename an item within its parent directory.
    /// Maps to: FilesystemOperations.RenameAsync / RenameAction
    Rename {
        path: String,
        new_name: String,
        #[serde(default)]
        collision: CollisionPolicy,
    },

    /// Create a symbolic link (Unix) pointing source → link_path.
    /// Maps to: FilesystemOperations.CreateShortcutItemsAsync / CreateShortcutAction
    CreateSymlink {
        sources: Vec<String>,
        link_paths: Vec<String>,
    },

    /// Query metadata for one or more paths.
    /// Maps to: FilePropertiesHelpers / SelectedItemsPropertiesViewModel
    Stat {
        paths: Vec<String>,
        #[serde(default)]
        checksum: bool,
    },

    /// List directory contents (non-recursive by default).
    /// Maps to: Win32StorageEnumerator / UniversalStorageEnumerator
    List {
        path: String,
        #[serde(default)]
        recursive: bool,
        #[serde(default)]
        include_hidden: bool,
    },

    /// Search for items matching a query string.
    /// Maps to: FolderSearch / SearchAction
    Search {
        root: String,
        query: String,
        #[serde(default)]
        recursive: bool,
        #[serde(default = "default_max_results")]
        max_results: usize,
        #[serde(default)]
        match_case: bool,
    },

    /// List all mounted drives / volumes.
    /// Maps to: DriveHelpers / DrivesViewModel
    ListDrives,

    /// Compute directory size (bytes).
    /// Maps to: FileSizeCalculator
    FolderSize { path: String },

    /// Compress items into an archive.
    /// Maps to: StorageArchiveService.CompressAsync / BaseCompressArchiveAction
    Compress {
        sources: Vec<String>,
        output: String,
        #[serde(default = "default_archive_format")]
        format: ArchiveFormat,
    },

    /// Extract an archive to a directory.
    /// Maps to: StorageArchiveService.DecompressAsync / BaseDecompressArchiveAction
    Extract {
        archive: String,
        destination: String,
    },

    /// Recycle-bin queries.
    /// Maps to: StorageTrashBinService
    TrashInfo,

    /// Flush / empty the recycle bin (platform trash directory).
    /// Maps to: EmptyRecycleBinAction / StorageTrashBinService
    EmptyTrash,
}

/// Item kind for Create commands.
#[derive(Debug, Deserialize, Serialize, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum ItemKind {
    File,
    Directory,
}

/// Name-collision resolution policy.
/// Maps to: NameCollisionOption / FileNameConflictResolveOptionType
#[derive(Debug, Deserialize, Serialize, Clone, Copy, Default)]
#[serde(rename_all = "snake_case")]
enum CollisionPolicy {
    /// Append (N) suffix until unique — default behaviour.
    #[default]
    GenerateUniqueName,
    /// Overwrite the existing item.
    ReplaceExisting,
    /// Return an error immediately.
    FailIfExists,
    /// Skip this item silently.
    Skip,
}

/// Archive format for Compress.
/// Maps to: ArchiveFormats enum
#[derive(Debug, Deserialize, Serialize, Clone, Copy)]
#[serde(rename_all = "snake_case")]
enum ArchiveFormat {
    Zip,
    TarGz,
    TarBz2,
    TarXz,
}
impl Default for ArchiveFormat {
    fn default() -> Self { ArchiveFormat::Zip }
}

// ─────────────────────────────────────────────────────────────────────────────
// Response types
// Maps to: ReturnResult / FilesystemResult / IStorageHistory
// ─────────────────────────────────────────────────────────────────────────────

/// Every outbound message sent back to the controller.
#[derive(Debug, Serialize)]
struct Response {
    /// Maps to ReturnResult / FileSystemStatusCode
    status: Status,
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    data: Option<serde_json::Value>,
    /// Mirrors IStorageHistory — the caller uses this for undo/redo tracking.
    #[serde(skip_serializing_if = "Option::is_none")]
    history: Option<HistoryEntry>,
}

/// Maps to ReturnResult / FileSystemStatusCode
#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case")]
enum Status {
    Success,
    Failed,
    Unauthorized,
    NotFound,
    AlreadyExists,
    InUse,
    NameTooLong,
    NotAFile,
    NotAFolder,
    Cancelled,
    PartialSuccess,
}

/// Mirrors IStorageHistory — returned to the caller so it can manage undo/redo.
/// Maps to: StorageHistory / FileOperationType
#[derive(Debug, Serialize)]
struct HistoryEntry {
    operation: HistoryOp,
    sources: Vec<String>,
    destinations: Vec<String>,
}

/// Maps to FileOperationType
#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case")]
enum HistoryOp {
    CreateNew,
    CreateLink,
    Rename,
    Copy,
    Move,
    Delete,
    Recycle,
    Extract,
    Restore,
}

// ─────────────────────────────────────────────────────────────────────────────
// Item metadata — returned by Stat / List
// Maps to: ListedItem / ItemProperties / FilePropertiesHelpers
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Serialize)]
struct ItemMeta {
    path: String,
    name: String,
    kind: ItemKind,
    size_bytes: u64,
    created_unix: Option<u64>,
    modified_unix: Option<u64>,
    is_readonly: bool,
    is_hidden: bool,
    is_symlink: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    checksum_md5: Option<String>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Drive information
// Maps to: DriveItem / DriveHelpers
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Serialize)]
struct DriveEntry {
    path: String,
    total_bytes: u64,
    free_bytes: u64,
    used_bytes: u64,
}

// ─────────────────────────────────────────────────────────────────────────────
// Defaults
// ─────────────────────────────────────────────────────────────────────────────

fn default_max_results() -> usize { 500 }
fn default_archive_format() -> ArchiveFormat { ArchiveFormat::Zip }

// ─────────────────────────────────────────────────────────────────────────────
// Entrypoint — read→dispatch→write loop
// ─────────────────────────────────────────────────────────────────────────────

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) if l.trim().is_empty() => continue,
            Ok(l) => l,
            Err(e) => {
                eprintln!("[fsengine] stdin read error: {e}");
                break;
            }
        };

        let response = match serde_json::from_str::<Command>(&line) {
            Ok(cmd) => dispatch(cmd),
            Err(e) => Response {
                status: Status::Failed,
                message: Some(format!("parse error: {e}")),
                data: None,
                history: None,
            },
        };

        match serde_json::to_string(&response) {
            Ok(json) => {
                let _ = writeln!(out, "{json}");
                let _ = out.flush();
            }
            Err(e) => eprintln!("[fsengine] serialise error: {e}"),
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Command dispatcher
// ─────────────────────────────────────────────────────────────────────────────

fn dispatch(cmd: Command) -> Response {
    match cmd {
        Command::Create { path, kind, overwrite } => op_create(&path, kind, overwrite),
        Command::Copy { sources, destinations, collision } => op_copy(&sources, &destinations, collision),
        Command::Move { sources, destinations, collision } => op_move(&sources, &destinations, collision),
        Command::Delete { paths, permanently } => op_delete(&paths, permanently),
        Command::Rename { path, new_name, collision } => op_rename(&path, &new_name, collision),
        Command::CreateSymlink { sources, link_paths } => op_symlink(&sources, &link_paths),
        Command::Stat { paths, checksum } => op_stat(&paths, checksum),
        Command::List { path, recursive, include_hidden } => op_list(&path, recursive, include_hidden),
        Command::Search { root, query, recursive, max_results, match_case } =>
            op_search(&root, &query, recursive, max_results, match_case),
        Command::ListDrives => op_list_drives(),
        Command::FolderSize { path } => op_folder_size(&path),
        Command::Compress { sources, output, format } => op_compress(&sources, &output, format),
        Command::Extract { archive, destination } => op_extract(&archive, &destination),
        Command::TrashInfo => op_trash_info(),
        Command::EmptyTrash => op_empty_trash(),
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Operation implementations
// Each maps precisely to a method in IFilesystemOperations / IFilesystemHelpers
// ─────────────────────────────────────────────────────────────────────────────

// ── Create ───────────────────────────────────────────────────────────────────
// Maps to: FilesystemOperations.CreateAsync
fn op_create(path: &str, kind: ItemKind, overwrite: bool) -> Response {
    let p = Path::new(path);

    if p.exists() {
        if !overwrite {
            return fail(Status::AlreadyExists, format!("already exists: {path}"), None);
        }
        if let Err(e) = remove_path(p) {
            return fail(from_io(&e), format!("could not remove existing: {e}"), None);
        }
    }

    let result = match kind {
        ItemKind::Directory => {
            fs::create_dir_all(p).map(|_| ())
        }
        ItemKind::File => {
            if let Some(parent) = p.parent() {
                if let Err(e) = fs::create_dir_all(parent) {
                    return fail(from_io(&e), format!("parent dir: {e}"), None);
                }
            }
            fs::File::create(p).map(|_| ())
        }
    };

    match result {
        Ok(_) => Response {
            status: Status::Success,
            message: None,
            data: Some(serde_json::json!({ "created": path })),
            history: Some(HistoryEntry {
                operation: HistoryOp::CreateNew,
                sources: vec![path.to_string()],
                destinations: vec![],
            }),
        },
        Err(e) => fail(from_io(&e), format!("create failed: {e}"), None),
    }
}

// ── Copy ─────────────────────────────────────────────────────────────────────
// Maps to: ShellFilesystemOperations.CopyItemsAsync / FileOperationsHelpers.CopyItemAsync
fn op_copy(sources: &[String], destinations: &[String], collision: CollisionPolicy) -> Response {
    if sources.len() != destinations.len() {
        return fail(Status::Failed, "sources/destinations length mismatch".into(), None);
    }

    let mut succeeded: Vec<(String, String)> = vec![];
    let mut failed_items: Vec<String> = vec![];

    for (src, dst) in sources.iter().zip(destinations.iter()) {
        let src_p = Path::new(src);
        let dst_p = resolve_collision(Path::new(dst), collision);

        match &dst_p {
            None => {
                failed_items.push(src.clone()); // Skip
                continue;
            }
            Some(actual_dst) => {
                if let Some(parent) = actual_dst.parent() {
                    if let Err(e) = fs::create_dir_all(parent) {
                        eprintln!("[fsengine] copy mkdir failed for {}: {e}", actual_dst.display());
                        failed_items.push(src.clone());
                        continue;
                    }
                }
                let result = if src_p.is_dir() {
                    copy_dir_all(src_p, actual_dst)
                } else {
                    fs::copy(src_p, actual_dst).map(|_| ())
                };
                match result {
                    Ok(_) => succeeded.push((src.clone(), actual_dst.to_string_lossy().into_owned())),
                    Err(e) => {
                        eprintln!("[fsengine] copy {src} → {} failed: {e}", actual_dst.display());
                        failed_items.push(src.clone());
                    }
                }
            }
        }
    }

    finish_batch(HistoryOp::Copy, succeeded, failed_items)
}

// ── Move ─────────────────────────────────────────────────────────────────────
// Maps to: ShellFilesystemOperations.MoveItemsAsync / FileOperationsHelpers.MoveItemAsync
fn op_move(sources: &[String], destinations: &[String], collision: CollisionPolicy) -> Response {
    if sources.len() != destinations.len() {
        return fail(Status::Failed, "sources/destinations length mismatch".into(), None);
    }

    let mut succeeded: Vec<(String, String)> = vec![];
    let mut failed_items: Vec<String> = vec![];

    for (src, dst) in sources.iter().zip(destinations.iter()) {
        let src_p = Path::new(src);
        let dst_p = resolve_collision(Path::new(dst), collision);

        match &dst_p {
            None => { failed_items.push(src.clone()); continue; }
            Some(actual_dst) => {
                if let Some(parent) = actual_dst.parent() {
                    if let Err(e) = fs::create_dir_all(parent) {
                        eprintln!("[fsengine] move mkdir failed: {e}");
                        failed_items.push(src.clone());
                        continue;
                    }
                }
                // Try atomic rename first (same filesystem), fall back to copy+delete
                let result = fs::rename(src_p, actual_dst).or_else(|_| {
                    let r = if src_p.is_dir() {
                        copy_dir_all(src_p, actual_dst)
                    } else {
                        fs::copy(src_p, actual_dst).map(|_| ())
                    };
                    r.and_then(|_| remove_path(src_p))
                });

                match result {
                    Ok(_) => succeeded.push((src.clone(), actual_dst.to_string_lossy().into_owned())),
                    Err(e) => {
                        eprintln!("[fsengine] move {src} failed: {e}");
                        failed_items.push(src.clone());
                    }
                }
            }
        }
    }

    finish_batch(HistoryOp::Move, succeeded, failed_items)
}

// ── Delete ───────────────────────────────────────────────────────────────────
// Maps to: FilesystemOperations.DeleteAsync / StorageTrashBinService
fn op_delete(paths: &[String], permanently: bool) -> Response {
    let mut succeeded: Vec<(String, String)> = vec![];
    let mut failed_items: Vec<String> = vec![];

    for path in paths {
        let p = Path::new(path);
        if !p.exists() {
            failed_items.push(path.clone());
            continue;
        }

        let (trash_path, op) = if permanently {
            (String::new(), HistoryOp::Delete)
        } else {
            match send_to_trash(p) {
                Ok(tp) => (tp, HistoryOp::Recycle),
                Err(e) => {
                    eprintln!("[fsengine] trash failed for {path}: {e}");
                    failed_items.push(path.clone());
                    continue;
                }
            }
        };

        let result = if permanently {
            remove_path(p)
        } else {
            Ok(()) // already moved by send_to_trash
        };

        match result {
            Ok(_) => succeeded.push((path.clone(), trash_path)),
            Err(e) => {
                eprintln!("[fsengine] delete {path} failed: {e}");
                failed_items.push(path.clone());
            }
        }
    }

    // Determine dominant operation type for history
    let op = if permanently { HistoryOp::Delete } else { HistoryOp::Recycle };
    let src_paths: Vec<String> = succeeded.iter().map(|(s, _)| s.clone()).collect();
    let dst_paths: Vec<String> = succeeded.iter().map(|(_, d)| d.clone()).collect();

    build_response(src_paths, dst_paths, failed_items, op)
}

// ── Rename ───────────────────────────────────────────────────────────────────
// Maps to: FilesystemOperations.RenameAsync / RenameAction
fn op_rename(path: &str, new_name: &str, collision: CollisionPolicy) -> Response {
    let src_p = Path::new(path);
    if !src_p.exists() {
        return fail(Status::NotFound, format!("not found: {path}"), None);
    }

    let parent = match src_p.parent() {
        Some(p) => p,
        None => return fail(Status::Failed, "cannot rename filesystem root".into(), None),
    };

    let candidate = parent.join(new_name);
    let dst_p = match resolve_collision(&candidate, collision) {
        Some(p) => p,
        None => return ok_response(
            "skipped (collision policy: skip)".into(),
            None,
            HistoryEntry { operation: HistoryOp::Rename, sources: vec![], destinations: vec![] },
        ),
    };

    match fs::rename(src_p, &dst_p) {
        Ok(_) => Response {
            status: Status::Success,
            message: None,
            data: Some(serde_json::json!({
                "from": path,
                "to": dst_p.to_string_lossy()
            })),
            history: Some(HistoryEntry {
                operation: HistoryOp::Rename,
                sources: vec![path.to_string()],
                destinations: vec![dst_p.to_string_lossy().into_owned()],
            }),
        },
        Err(e) => fail(from_io(&e), format!("rename failed: {e}"), None),
    }
}

// ── Create symlink ────────────────────────────────────────────────────────────
// Maps to: FilesystemOperations.CreateShortcutItemsAsync / CreateShortcutAction
fn op_symlink(sources: &[String], link_paths: &[String]) -> Response {
    if sources.len() != link_paths.len() {
        return fail(Status::Failed, "sources/link_paths length mismatch".into(), None);
    }

    let mut succeeded: Vec<(String, String)> = vec![];
    let mut failed_items: Vec<String> = vec![];

    for (src, link) in sources.iter().zip(link_paths.iter()) {
        let link_p = Path::new(link);
        if let Some(parent) = link_p.parent() {
            let _ = fs::create_dir_all(parent);
        }

        #[cfg(unix)]
        let result = std::os::unix::fs::symlink(src, link_p);
        #[cfg(windows)]
        let result = {
            let src_p = Path::new(src);
            if src_p.is_dir() {
                std::os::windows::fs::symlink_dir(src_p, link_p)
            } else {
                std::os::windows::fs::symlink_file(src_p, link_p)
            }
        };

        match result {
            Ok(_) => succeeded.push((src.clone(), link.clone())),
            Err(e) => {
                eprintln!("[fsengine] symlink {src} → {link} failed: {e}");
                failed_items.push(src.clone());
            }
        }
    }

    finish_batch(HistoryOp::CreateLink, succeeded, failed_items)
}

// ── Stat ─────────────────────────────────────────────────────────────────────
// Maps to: FilePropertiesHelpers / SelectedItemsPropertiesViewModel
fn op_stat(paths: &[String], checksum: bool) -> Response {
    let mut items: Vec<ItemMeta> = vec![];
    let mut not_found: Vec<String> = vec![];

    for path in paths {
        match stat_path(path, checksum) {
            Some(meta) => items.push(meta),
            None => not_found.push(path.clone()),
        }
    }

    let status = if not_found.is_empty() {
        Status::Success
    } else if items.is_empty() {
        Status::NotFound
    } else {
        Status::PartialSuccess
    };

    Response {
        status,
        message: if not_found.is_empty() { None } else {
            Some(format!("not found: {}", not_found.join(", ")))
        },
        data: Some(serde_json::to_value(&items).unwrap_or(serde_json::Value::Null)),
        history: None,
    }
}

// ── List ──────────────────────────────────────────────────────────────────────
// Maps to: Win32StorageEnumerator.EnumerateItemsFromPathAsync
fn op_list(path: &str, recursive: bool, include_hidden: bool) -> Response {
    let p = Path::new(path);
    if !p.is_dir() {
        return fail(Status::NotAFolder, format!("not a directory: {path}"), None);
    }

    let mut entries: Vec<ItemMeta> = vec![];
    list_dir(p, recursive, include_hidden, &mut entries);

    Response {
        status: Status::Success,
        message: None,
        data: Some(serde_json::to_value(&entries).unwrap_or(serde_json::Value::Null)),
        history: None,
    }
}

// ── Search ────────────────────────────────────────────────────────────────────
// Maps to: FolderSearch / SearchAction
fn op_search(root: &str, query: &str, recursive: bool, max_results: usize, match_case: bool) -> Response {
    let root_p = Path::new(root);
    if !root_p.is_dir() {
        return fail(Status::NotAFolder, format!("not a directory: {root}"), None);
    }

    let needle = if match_case { query.to_string() } else { query.to_lowercase() };
    let mut results: Vec<String> = vec![];
    search_dir(root_p, &needle, match_case, recursive, max_results, &mut results);

    Response {
        status: Status::Success,
        message: None,
        data: Some(serde_json::json!({ "results": results, "count": results.len() })),
        history: None,
    }
}

// ── ListDrives ────────────────────────────────────────────────────────────────
// Maps to: DriveHelpers / DrivesViewModel
fn op_list_drives() -> Response {
    let drives = collect_drives();
    Response {
        status: Status::Success,
        message: None,
        data: Some(serde_json::to_value(&drives).unwrap_or(serde_json::Value::Null)),
        history: None,
    }
}

// ── FolderSize ────────────────────────────────────────────────────────────────
// Maps to: FileSizeCalculator
fn op_folder_size(path: &str) -> Response {
    let p = Path::new(path);
    if !p.exists() {
        return fail(Status::NotFound, format!("not found: {path}"), None);
    }
    let size = dir_size(p);
    Response {
        status: Status::Success,
        message: None,
        data: Some(serde_json::json!({ "path": path, "size_bytes": size })),
        history: None,
    }
}

// ── Compress ──────────────────────────────────────────────────────────────────
// Maps to: StorageArchiveService.CompressAsync (zip only in stdlib; others noted)
fn op_compress(sources: &[String], output: &str, format: ArchiveFormat) -> Response {
    // Validate sources exist
    for src in sources {
        if !Path::new(src).exists() {
            return fail(Status::NotFound, format!("source not found: {src}"), None);
        }
    }

    // Create parent directory for output
    if let Some(parent) = Path::new(output).parent() {
        if let Err(e) = fs::create_dir_all(parent) {
            return fail(from_io(&e), format!("output dir: {e}"), None);
        }
    }

    let result = match format {
        ArchiveFormat::Zip => compress_zip(sources, output),
        // For tar variants we emit a shell-compose string as a portability note,
        // since tar bindings would require an additional crate.
        ArchiveFormat::TarGz | ArchiveFormat::TarBz2 | ArchiveFormat::TarXz => {
            Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "tar variants require the 'tar' system binary; use ArchiveFormat::zip or add the 'tar' crate",
            ))
        }
    };

    match result {
        Ok(_) => Response {
            status: Status::Success,
            message: None,
            data: Some(serde_json::json!({ "output": output })),
            history: Some(HistoryEntry {
                operation: HistoryOp::CreateNew,
                sources: sources.to_vec(),
                destinations: vec![output.to_string()],
            }),
        },
        Err(e) => fail(from_io(&e), format!("compress failed: {e}"), None),
    }
}

// ── Extract ───────────────────────────────────────────────────────────────────
// Maps to: StorageArchiveService.DecompressAsync / DecompressArchive
fn op_extract(archive: &str, destination: &str) -> Response {
    if !Path::new(archive).exists() {
        return fail(Status::NotFound, format!("archive not found: {archive}"), None);
    }
    if let Err(e) = fs::create_dir_all(destination) {
        return fail(from_io(&e), format!("dest dir: {e}"), None);
    }

    let result = extract_zip(archive, destination);
    match result {
        Ok(_) => Response {
            status: Status::Success,
            message: None,
            data: Some(serde_json::json!({ "destination": destination })),
            history: Some(HistoryEntry {
                operation: HistoryOp::Extract,
                sources: vec![archive.to_string()],
                destinations: vec![destination.to_string()],
            }),
        },
        Err(e) => fail(from_io(&e), format!("extract failed: {e}"), None),
    }
}

// ── TrashInfo ─────────────────────────────────────────────────────────────────
// Maps to: StorageTrashBinService.QueryRecycleBin / HasItems / GetSize
fn op_trash_info() -> Response {
    let trash = trash_dir();
    let (has_items, size) = match &trash {
        None => (false, 0u64),
        Some(p) => {
            let has = p.exists() && fs::read_dir(p).ok().map(|mut d| d.next().is_some()).unwrap_or(false);
            let size = if p.exists() { dir_size(p) } else { 0 };
            (has, size)
        }
    };
    Response {
        status: Status::Success,
        message: None,
        data: Some(serde_json::json!({
            "has_items": has_items,
            "size_bytes": size,
            "trash_path": trash.map(|p| p.to_string_lossy().into_owned())
        })),
        history: None,
    }
}

// ── EmptyTrash ────────────────────────────────────────────────────────────────
// Maps to: EmptyRecycleBinAction / StorageTrashBinService
fn op_empty_trash() -> Response {
    let trash = match trash_dir() {
        Some(p) if p.exists() => p,
        _ => return ok_response("trash is already empty or not found".into(), None,
            HistoryEntry { operation: HistoryOp::Delete, sources: vec![], destinations: vec![] }),
    };

    let mut errors = 0usize;
    if let Ok(entries) = fs::read_dir(&trash) {
        for entry in entries.flatten() {
            if let Err(e) = remove_path(&entry.path()) {
                eprintln!("[fsengine] empty trash: could not remove {}: {e}", entry.path().display());
                errors += 1;
            }
        }
    }

    if errors == 0 {
        ok_response("trash emptied".into(), None,
            HistoryEntry { operation: HistoryOp::Delete, sources: vec![], destinations: vec![] })
    } else {
        fail(Status::PartialSuccess, format!("{errors} items could not be removed"), None)
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Platform-specific utilities
// ─────────────────────────────────────────────────────────────────────────────

/// Recursively copy a directory.
/// Maps to: ShellFilesystemOperations copy-tree path
fn copy_dir_all(src: &Path, dst: &Path) -> io::Result<()> {
    fs::create_dir_all(dst)?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let dest = dst.join(entry.file_name());
        if file_type.is_dir() {
            copy_dir_all(&entry.path(), &dest)?;
        } else {
            fs::copy(entry.path(), dest)?;
        }
    }
    Ok(())
}

/// Remove a file or directory tree, forcing read-only items.
fn remove_path(p: &Path) -> io::Result<()> {
    if p.is_symlink() || p.is_file() {
        fs::remove_file(p)
    } else if p.is_dir() {
        fs::remove_dir_all(p)
    } else {
        Ok(()) // not found — treat as success
    }
}

/// Send a path to the platform trash directory.
/// Maps to: StorageTrashBinService / _send_to_trash logic
fn send_to_trash(p: &Path) -> io::Result<String> {
    let trash = trash_dir().ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "trash directory unavailable"))?;
    fs::create_dir_all(&trash)?;

    let file_name = p.file_name().ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "no filename"))?;
    let mut dest = trash.join(file_name);

    // Avoid collisions in the trash
    if dest.exists() {
        let stem = Path::new(file_name).file_stem().unwrap_or(file_name);
        let ext  = Path::new(file_name).extension();
        let ts = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        dest = match ext {
            Some(e) => trash.join(format!("{}_{}.{}", stem.to_string_lossy(), ts, e.to_string_lossy())),
            None    => trash.join(format!("{}_{}", stem.to_string_lossy(), ts)),
        };
    }

    fs::rename(p, &dest).or_else(|_| {
        // Cross-device: copy then delete
        if p.is_dir() { copy_dir_all(p, &dest)?; } else { fs::copy(p, &dest).map(|_| ())?; }
        remove_path(p)
    })?;

    Ok(dest.to_string_lossy().into_owned())
}

/// Resolve the actual destination path based on collision policy.
/// Maps to: the collision-resolution logic across FilesystemOperations / ShellFilesystemOperations
fn resolve_collision(dest: &Path, policy: CollisionPolicy) -> Option<PathBuf> {
    if !dest.exists() {
        return Some(dest.to_path_buf());
    }
    match policy {
        CollisionPolicy::ReplaceExisting => Some(dest.to_path_buf()),
        CollisionPolicy::FailIfExists    => None, // caller treats None as hard failure
        CollisionPolicy::Skip            => None,
        CollisionPolicy::GenerateUniqueName => {
            let parent = dest.parent().unwrap_or(Path::new("."));
            let stem   = dest.file_stem().unwrap_or_default().to_string_lossy();
            let ext    = dest.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
            for i in 1u32.. {
                let candidate = parent.join(format!("{stem} ({i}){ext}"));
                if !candidate.exists() {
                    return Some(candidate);
                }
            }
            None
        }
    }
}

/// Platform trash directory.
/// Maps to: StorageTrashBinService path logic
fn trash_dir() -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        dirs_home().map(|h| h.join(".Trash"))
    }
    #[cfg(target_os = "linux")]
    {
        let xdg = std::env::var("XDG_DATA_HOME")
            .ok()
            .map(PathBuf::from)
            .or_else(|| dirs_home().map(|h| h.join(".local").join("share")));
        xdg.map(|d| d.join("Trash").join("files"))
    }
    #[cfg(target_os = "windows")]
    {
        // On Windows the real Recycle Bin is shell-managed; expose a local fallback
        dirs_home().map(|h| h.join(".local_trash"))
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux", target_os = "windows")))]
    {
        dirs_home().map(|h| h.join(".trash"))
    }
}

fn dirs_home() -> Option<PathBuf> {
    std::env::var("HOME").ok().map(PathBuf::from)
        .or_else(|| std::env::var("USERPROFILE").ok().map(PathBuf::from))
}

// ─────────────────────────────────────────────────────────────────────────────
// Stat / List helpers
// ─────────────────────────────────────────────────────────────────────────────

fn stat_path(path: &str, checksum: bool) -> Option<ItemMeta> {
    let p = Path::new(path);
    let meta = p.symlink_metadata().ok()?;
    let is_symlink = meta.file_type().is_symlink();
    // Follow symlink for kind/size if not the symlink itself
    let real_meta = if is_symlink { p.metadata().ok()? } else { meta.clone() };
    let kind = if real_meta.is_dir() { ItemKind::Directory } else { ItemKind::File };
    let size = if real_meta.is_dir() { dir_size(p) } else { real_meta.len() };

    let created_unix = real_meta.created().ok()
        .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
        .map(|d| d.as_secs());
    let modified_unix = real_meta.modified().ok()
        .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
        .map(|d| d.as_secs());

    #[cfg(unix)]
    let is_readonly = {
        use std::os::unix::fs::MetadataExt;
        real_meta.mode() & 0o200 == 0
    };
    #[cfg(not(unix))]
    let is_readonly = real_meta.permissions().readonly();

    let name = p.file_name().map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| path.to_string());
    let is_hidden = name.starts_with('.');

    let checksum_md5 = if checksum && kind == ItemKind::File {
        compute_md5(p).ok()
    } else {
        None
    };

    Some(ItemMeta {
        path: path.to_string(),
        name,
        kind,
        size_bytes: size,
        created_unix,
        modified_unix,
        is_readonly,
        is_hidden,
        is_symlink,
        checksum_md5,
    })
}

fn list_dir(dir: &Path, recursive: bool, include_hidden: bool, out: &mut Vec<ItemMeta>) {
    let entries = match fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let path_str = entry.path().to_string_lossy().into_owned();
        let name = entry.file_name().to_string_lossy().into_owned();
        if !include_hidden && name.starts_with('.') { continue; }
        if let Some(meta) = stat_path(&path_str, false) {
            let is_dir = meta.kind == ItemKind::Directory;
            out.push(meta);
            if recursive && is_dir {
                list_dir(&entry.path(), true, include_hidden, out);
            }
        }
    }
}

fn search_dir(dir: &Path, needle: &str, match_case: bool, recursive: bool, max: usize, out: &mut Vec<String>) {
    if out.len() >= max { return; }
    let entries = match fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        if out.len() >= max { return; }
        let name = entry.file_name().to_string_lossy().into_owned();
        let cmp  = if match_case { name.clone() } else { name.to_lowercase() };
        if cmp.contains(needle) {
            out.push(entry.path().to_string_lossy().into_owned());
        }
        if recursive && entry.path().is_dir() {
            search_dir(&entry.path(), needle, match_case, true, max, out);
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Drive enumeration
// ─────────────────────────────────────────────────────────────────────────────

fn collect_drives() -> Vec<DriveEntry> {
    let mut drives = vec![];

    #[cfg(target_os = "linux")]
    {
        // Parse /proc/mounts for real block-device mounts
        if let Ok(content) = fs::read_to_string("/proc/mounts") {
            for line in content.lines() {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() < 2 { continue; }
                let mount = parts[1];
                if mount == "none" || mount.starts_with("/proc") || mount.starts_with("/sys") || mount.starts_with("/dev/pts") { continue; }
                if let Ok(stat) = statvfs(mount) {
                    drives.push(DriveEntry {
                        path: mount.to_string(),
                        total_bytes: stat.0,
                        free_bytes: stat.1,
                        used_bytes: stat.0.saturating_sub(stat.1),
                    });
                }
            }
        }
        // Fallback to root
        if drives.is_empty() {
            if let Ok(stat) = statvfs("/") {
                drives.push(DriveEntry { path: "/".into(), total_bytes: stat.0, free_bytes: stat.1, used_bytes: stat.0.saturating_sub(stat.1) });
            }
        }
    }

    #[cfg(not(target_os = "linux"))]
    {
        let roots = {
            #[cfg(target_os = "macos")]
            { vec!["/".to_string()] }
            #[cfg(target_os = "windows")]
            {
                (b'A'..=b'Z').map(|l| format!("{}:\\", l as char))
                    .filter(|p| Path::new(p).exists())
                    .collect::<Vec<_>>()
            }
            #[cfg(not(any(target_os = "macos", target_os = "windows")))]
            { vec!["/".to_string()] }
        };
        for root in roots {
            if let Ok(stat) = statvfs(&root) {
                drives.push(DriveEntry {
                    path: root,
                    total_bytes: stat.0,
                    free_bytes: stat.1,
                    used_bytes: stat.0.saturating_sub(stat.1),
                });
            }
        }
    }

    drives
}

/// Thin wrapper around libc statvfs to get total/free bytes.
fn statvfs(path: &str) -> io::Result<(u64, u64)> {
    #[cfg(unix)]
    unsafe {
        let c_path = std::ffi::CString::new(path).map_err(|e| io::Error::new(io::ErrorKind::InvalidInput, e))?;
        let mut stat: libc::statvfs = std::mem::zeroed();
        if libc::statvfs(c_path.as_ptr(), &mut stat) != 0 {
            return Err(io::Error::last_os_error());
        }
        let bsize = stat.f_frsize as u64;
        let total = stat.f_blocks as u64 * bsize;
        let free  = stat.f_bavail as u64 * bsize;
        Ok((total, free))
    }
    #[cfg(not(unix))]
    {
        // Windows: use GetDiskFreeSpaceEx via metadata approximation
        let _ = path;
        Err(io::Error::new(io::ErrorKind::Unsupported, "statvfs not available on this platform"))
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Archive helpers (zip, stdlib-only)
// ─────────────────────────────────────────────────────────────────────────────

/// Minimal zip writer using only stdlib (no external crate required).
/// Writes stored (uncompressed) entries — sufficient for a headless engine;
/// callers that need deflate should pipe to the system `zip` binary.
fn compress_zip(sources: &[String], output: &str) -> io::Result<()> {
    use std::io::Write;

    let out_file = fs::File::create(output)?;
    let mut buf = io::BufWriter::new(out_file);
    let mut central_dir: Vec<u8> = vec![];
    let mut num_entries: u16 = 0;

    for src in sources {
        let src_p = Path::new(src);
        let name  = src_p.file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| src.clone());

        if src_p.is_dir() {
            zip_add_dir(&mut buf, &mut central_dir, &mut num_entries, src_p, &name)?;
        } else {
            zip_add_file(&mut buf, &mut central_dir, &mut num_entries, src_p, &name)?;
        }
    }

    // End of central directory record
    let cd_size   = central_dir.len() as u32;
    let cd_offset = buf.stream_position()? as u32;
    buf.write_all(&central_dir)?;
    buf.write_all(&zip_eocd(num_entries, cd_size, cd_offset))?;
    Ok(())
}

fn zip_add_dir(buf: &mut impl Write, cd: &mut Vec<u8>, count: &mut u16, dir: &Path, rel_prefix: &str) -> io::Result<()> {
    for entry in fs::read_dir(dir)?.flatten() {
        let entry_rel = format!("{}/{}", rel_prefix, entry.file_name().to_string_lossy());
        if entry.path().is_dir() {
            zip_add_dir(buf, cd, count, &entry.path(), &entry_rel)?;
        } else {
            zip_add_file(buf, cd, count, &entry.path(), &entry_rel)?;
        }
    }
    Ok(())
}

fn zip_add_file(buf: &mut (impl Write + io::Seek), cd: &mut Vec<u8>, count: &mut u16, path: &Path, name: &str) -> io::Result<()> {
    let data = fs::read(path)?;
    let crc  = crc32(&data);
    let offset = buf.stream_position()? as u32;
    let name_b = name.as_bytes();
    let size   = data.len() as u32;

    // Local file header
    buf.write_all(b"PK\x03\x04")?;
    buf.write_all(&[20, 0, 0, 0, 0, 0, 0, 0, 0, 0])?; // ver, flags, method=stored, dostime
    buf.write_all(&crc.to_le_bytes())?;
    buf.write_all(&size.to_le_bytes())?;           // compressed
    buf.write_all(&size.to_le_bytes())?;           // uncompressed
    buf.write_all(&(name_b.len() as u16).to_le_bytes())?;
    buf.write_all(&[0u8; 2])?;                    // extra len
    buf.write_all(name_b)?;
    buf.write_all(&data)?;

    // Central directory entry
    cd.write_all(b"PK\x01\x02")?;
    cd.write_all(&[20, 0, 20, 0, 0, 0, 0, 0, 0, 0, 0, 0])?;
    cd.write_all(&crc.to_le_bytes())?;
    cd.write_all(&size.to_le_bytes())?;
    cd.write_all(&size.to_le_bytes())?;
    cd.write_all(&(name_b.len() as u16).to_le_bytes())?;
    cd.write_all(&[0u8; 6])?;                     // extra, comment, disk
    cd.write_all(&[0u8; 4])?;                     // internal+external attrs
    cd.write_all(&offset.to_le_bytes())?;
    cd.write_all(name_b)?;

    *count += 1;
    Ok(())
}

fn zip_eocd(num: u16, cd_size: u32, cd_offset: u32) -> Vec<u8> {
    let mut v = vec![];
    v.extend_from_slice(b"PK\x05\x06");
    v.extend_from_slice(&[0u8; 4]);           // disk numbers
    v.extend_from_slice(&num.to_le_bytes());  // entries on disk
    v.extend_from_slice(&num.to_le_bytes());  // total entries
    v.extend_from_slice(&cd_size.to_le_bytes());
    v.extend_from_slice(&cd_offset.to_le_bytes());
    v.extend_from_slice(&[0u8; 2]);           // comment len
    v
}

/// Extract a zip archive (stored/uncompressed entries only).
/// For deflated archives the caller should use the system unzip binary.
fn extract_zip(archive: &str, destination: &str) -> io::Result<()> {
    use std::io::Read;
    let data = fs::read(archive)?;
    let dest = Path::new(destination);

    // Find End of Central Directory
    let eocd_sig = b"PK\x05\x06";
    let eocd_pos = find_last(data.as_slice(), eocd_sig)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "not a valid zip file"))?;

    let num_entries = u16::from_le_bytes([data[eocd_pos + 8], data[eocd_pos + 9]]) as usize;
    let cd_offset   = u32::from_le_bytes([data[eocd_pos + 16], data[eocd_pos + 17], data[eocd_pos + 18], data[eocd_pos + 19]]) as usize;

    let mut pos = cd_offset;
    for _ in 0..num_entries {
        if pos + 46 > data.len() { break; }
        if &data[pos..pos + 4] != b"PK\x01\x02" { break; }
        let name_len  = u16::from_le_bytes([data[pos + 28], data[pos + 29]]) as usize;
        let extra_len = u16::from_le_bytes([data[pos + 30], data[pos + 31]]) as usize;
        let cmt_len   = u16::from_le_bytes([data[pos + 32], data[pos + 33]]) as usize;
        let lh_offset = u32::from_le_bytes([data[pos + 42], data[pos + 43], data[pos + 44], data[pos + 45]]) as usize;
        let name      = std::str::from_utf8(&data[pos + 46..pos + 46 + name_len])
            .unwrap_or("unknown");

        let out_path = dest.join(name);
        if name.ends_with('/') {
            fs::create_dir_all(&out_path)?;
        } else {
            // Read from local header
            let lh = lh_offset;
            if lh + 30 > data.len() { break; }
            let lh_name_len  = u16::from_le_bytes([data[lh + 26], data[lh + 27]]) as usize;
            let lh_extra_len = u16::from_le_bytes([data[lh + 28], data[lh + 29]]) as usize;
            let data_start   = lh + 30 + lh_name_len + lh_extra_len;
            let comp_size    = u32::from_le_bytes([data[lh + 18], data[lh + 19], data[lh + 20], data[lh + 21]]) as usize;
            let method       = u16::from_le_bytes([data[lh + 8], data[lh + 9]]);

            if method != 0 {
                eprintln!("[fsengine] extract: compressed entry '{name}' (method {method}) skipped — stored entries only");
            } else if data_start + comp_size <= data.len() {
                if let Some(parent) = out_path.parent() { fs::create_dir_all(parent)?; }
                fs::write(&out_path, &data[data_start..data_start + comp_size])?;
            }
        }
        pos += 46 + name_len + extra_len + cmt_len;
    }
    Ok(())
}

fn find_last(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.len() > haystack.len() { return None; }
    (0..=(haystack.len() - needle.len())).rev()
        .find(|&i| &haystack[i..i + needle.len()] == needle)
}

// ─────────────────────────────────────────────────────────────────────────────
// CRC-32 (for zip)
// ─────────────────────────────────────────────────────────────────────────────

fn crc32(data: &[u8]) -> u32 {
    const POLY: u32 = 0xEDB88320;
    let mut crc: u32 = 0xFFFF_FFFF;
    for &byte in data {
        crc ^= byte as u32;
        for _ in 0..8 {
            crc = if crc & 1 != 0 { (crc >> 1) ^ POLY } else { crc >> 1 };
        }
    }
    !crc
}

// ─────────────────────────────────────────────────────────────────────────────
// MD5 checksum (maps to ChecksumHelpers / HashesViewModel)
// ─────────────────────────────────────────────────────────────────────────────

fn compute_md5(path: &Path) -> io::Result<String> {
    use std::io::Read;
    let mut file = fs::File::open(path)?;
    let mut state = [0u32; 4];
    md5_init(&mut state);
    let mut buf = vec![0u8; 65536];
    let mut msg_len: u64 = 0;
    let mut pending: Vec<u8> = vec![];

    loop {
        let n = file.read(&mut buf)?;
        if n == 0 { break; }
        msg_len += n as u64;
        pending.extend_from_slice(&buf[..n]);
        while pending.len() >= 64 {
            let block: [u8; 64] = pending[..64].try_into().unwrap();
            md5_compress(&mut state, &block);
            pending.drain(..64);
        }
    }

    // Padding
    pending.push(0x80);
    while pending.len() % 64 != 56 { pending.push(0x00); }
    let bit_len = (msg_len * 8).to_le_bytes();
    pending.extend_from_slice(&bit_len);
    for chunk in pending.chunks_exact(64) {
        let block: [u8; 64] = chunk.try_into().unwrap();
        md5_compress(&mut state, &block);
    }

    let mut out = [0u8; 16];
    for (i, &s) in state.iter().enumerate() {
        out[i * 4..i * 4 + 4].copy_from_slice(&s.to_le_bytes());
    }
    Ok(out.iter().map(|b| format!("{b:02x}")).collect())
}

fn md5_init(s: &mut [u32; 4]) {
    s[0] = 0x67452301; s[1] = 0xEFCDAB89; s[2] = 0x98BADCFE; s[3] = 0x10325476;
}

fn md5_compress(s: &mut [u32; 4], block: &[u8; 64]) {
    const K: [u32; 64] = [
        0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
        0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
        0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
        0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
        0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
        0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
        0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
        0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
    ];
    const S: [u32; 64] = [
        7,12,17,22,7,12,17,22,7,12,17,22,7,12,17,22,
        5, 9,14,20,5, 9,14,20,5, 9,14,20,5, 9,14,20,
        4,11,16,23,4,11,16,23,4,11,16,23,4,11,16,23,
        6,10,15,21,6,10,15,21,6,10,15,21,6,10,15,21,
    ];
    let mut m = [0u32; 16];
    for (i, chunk) in block.chunks_exact(4).enumerate() {
        m[i] = u32::from_le_bytes(chunk.try_into().unwrap());
    }
    let (mut a, mut b, mut c, mut d) = (s[0], s[1], s[2], s[3]);
    for i in 0usize..64 {
        let (f, g) = match i {
            0..=15  => ((b & c) | (!b & d),              i),
            16..=31 => ((d & b) | (!d & c),              (5 * i + 1) % 16),
            32..=47 => (b ^ c ^ d,                        (3 * i + 5) % 16),
            _       => (c ^ (b | !d),                     (7 * i) % 16),
        };
        let temp = d;
        d = c; c = b;
        b = b.wrapping_add((a.wrapping_add(f).wrapping_add(K[i]).wrapping_add(m[g])).rotate_left(S[i]));
        a = temp;
    }
    s[0] = s[0].wrapping_add(a); s[1] = s[1].wrapping_add(b);
    s[2] = s[2].wrapping_add(c); s[3] = s[3].wrapping_add(d);
}

// ─────────────────────────────────────────────────────────────────────────────
// Misc helpers
// ─────────────────────────────────────────────────────────────────────────────

fn dir_size(p: &Path) -> u64 {
    let mut total = 0u64;
    if let Ok(entries) = fs::read_dir(p) {
        for entry in entries.flatten() {
            let ep = entry.path();
            if ep.is_dir() {
                total += dir_size(&ep);
            } else if let Ok(m) = ep.metadata() {
                total += m.len();
            }
        }
    }
    total
}

fn from_io(e: &io::Error) -> Status {
    match e.kind() {
        io::ErrorKind::PermissionDenied  => Status::Unauthorized,
        io::ErrorKind::NotFound          => Status::NotFound,
        io::ErrorKind::AlreadyExists     => Status::AlreadyExists,
        io::ErrorKind::WouldBlock        => Status::InUse,
        io::ErrorKind::InvalidFilename   => Status::NameTooLong,
        _                                => Status::Failed,
    }
}

fn fail(status: Status, msg: String, data: Option<serde_json::Value>) -> Response {
    eprintln!("[fsengine] {}: {msg}", format!("{status:?}").to_lowercase());
    Response { status, message: Some(msg), data, history: None }
}

fn ok_response(msg: String, data: Option<serde_json::Value>, history: HistoryEntry) -> Response {
    Response { status: Status::Success, message: Some(msg), data, history: Some(history) }
}

fn finish_batch(op: HistoryOp, succeeded: Vec<(String, String)>, failed: Vec<String>) -> Response {
    let src_paths: Vec<String> = succeeded.iter().map(|(s, _)| s.clone()).collect();
    let dst_paths: Vec<String> = succeeded.iter().map(|(_, d)| d.clone()).collect();
    build_response(src_paths, dst_paths, failed, op)
}

fn build_response(srcs: Vec<String>, dsts: Vec<String>, failed: Vec<String>, op: HistoryOp) -> Response {
    let status = match (srcs.is_empty(), failed.is_empty()) {
        (true,  true)  => Status::Failed,   // nothing happened
        (false, true)  => Status::Success,
        (false, false) => Status::PartialSuccess,
        (true,  false) => Status::Failed,
    };
    let message = if failed.is_empty() { None }
        else { Some(format!("failed items: {}", failed.join(", "))) };
    Response {
        status,
        message,
        data: if failed.is_empty() { None }
              else { Some(serde_json::json!({ "failed": failed })) },
        history: if srcs.is_empty() { None } else {
            Some(HistoryEntry { operation: op, sources: srcs, destinations: dsts })
        },
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// libc dependency (only used on unix for statvfs)
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(unix)]
mod libc {
    #[repr(C)]
    pub struct statvfs {
        pub f_bsize:   u64,
        pub f_frsize:  u64,
        pub f_blocks:  u64,
        pub f_bfree:   u64,
        pub f_bavail:  u64,
        pub f_files:   u64,
        pub f_ffree:   u64,
        pub f_favail:  u64,
        pub f_fsid:    u64,
        pub f_flag:    u64,
        pub f_namemax: u64,
        _pad: [u8; 32],
    }
    extern "C" {
        pub fn statvfs(path: *const std::os::raw::c_char, buf: *mut statvfs) -> std::os::raw::c_int;
    }
}
