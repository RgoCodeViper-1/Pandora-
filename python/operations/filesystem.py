"""
filesystem.py — Files App v4.1.1 Filesystem Engine (Python Port)
=================================================================
A cross-platform Python implementation of the core filesystem architecture
from the Files Community App (src/Files.App/Utils/Storage + Services/Storage).

Architecture mirrors:
  IFilesystemOperations  → FilesystemOperations (low-level, returns StorageHistory)
  IFilesystemHelpers     → FilesystemHelpers    (high-level, manages dialogs/history)
  IStorageHistory        → StorageHistory        (undo/redo log entries)
  StorageHistoryOperations → HistoryOperations  (undo/redo executor)
  StorageTrashBinService → TrashBinService      (recycle-bin queries)
  FilesystemResult       → FilesystemResult     (typed result wrapper)

Entrypoint
----------
Import this module and call `create_filesystem()` to get a configured
FilesystemManager ready for use:

    from filesystem import create_filesystem, FileOperationType, ReturnResult

    fs = create_filesystem()
    result = fs.copy("C:/source.txt", "D:/dest.txt")
    print(result)               # ReturnResult.Success / .Failed / etc.
    fs.undo()                   # undo the last operation
    fs.redo()                   # redo it

All public functions are also importable individually:
    from filesystem import copy_file, move_file, delete_file, rename_file, create_item

License: MIT (matches upstream Files Community project)
"""

from __future__ import annotations

import os
import shutil
import stat
import platform
import logging
import datetime
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Sequence

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)


# ===========================================================================
# Enumerations  (mirrors Files.App/Data/Enums)
# ===========================================================================

class ReturnResult(Enum):
    """Maps to Files.App.Data.Enums.ReturnResult"""
    InProgress = auto()
    Success = auto()
    Failed = auto()
    Cancelled = auto()
    UnknownException = auto()


class FileOperationType(Enum):
    """Maps to Files.App.Data.Enums.FileOperationType"""
    CreateNew = "CreateNew"
    CreateLink = "CreateLink"
    Rename = "Rename"
    Copy = "Copy"
    Move = "Move"
    Delete = "Delete"
    Recycle = "Recycle"
    Extract = "Extract"
    Restore = "Restore"


class FileSystemStatusCode(Enum):
    """Maps to Files.App.Data.Enums.FileSystemStatusCode"""
    Success = auto()
    Generic = auto()
    Unauthorized = auto()
    NotFound = auto()
    InUse = auto()
    NameAlreadyExists = auto()
    NotAFile = auto()
    NotAFolder = auto()
    AlreadyExists = auto()
    PropertyLoss = auto()


class FilesystemItemType(Enum):
    """Maps to Files.App.Data.Enums.FilesystemItemType"""
    File = auto()
    Directory = auto()
    Symlink = auto()


class NameCollisionOption(Enum):
    """Maps to Windows.Storage.NameCollisionOption"""
    GenerateUniqueName = auto()
    ReplaceExisting = auto()
    FailIfExists = auto()


class DeleteConfirmationPolicy(Enum):
    """Maps to Files.App.Data.Enums.DeleteConfirmationPolicy"""
    Never = auto()
    Always = auto()
    PermanentOnly = auto()


# ===========================================================================
# Core data models  (mirrors Files.App/Utils/Storage)
# ===========================================================================

@dataclass
class FilesystemResult:
    """
    Typed result wrapper.
    Maps to Files.App.Utils.Storage.FilesystemResult / FilesystemResult<T>.
    """
    error_code: FileSystemStatusCode = FileSystemStatusCode.Success
    result: object = None

    @property
    def success(self) -> bool:
        return self.error_code is FileSystemStatusCode.Success

    def __bool__(self) -> bool:
        return self.success

    @classmethod
    def ok(cls, result=None) -> "FilesystemResult":
        return cls(FileSystemStatusCode.Success, result)

    @classmethod
    def fail(cls, code: FileSystemStatusCode = FileSystemStatusCode.Generic, result=None) -> "FilesystemResult":
        return cls(code, result)

    def __repr__(self) -> str:
        return f"FilesystemResult(code={self.error_code.name}, ok={self.success})"


@dataclass
class StorageItemWithPath:
    """
    Lightweight pairing of a filesystem path with its item type.
    Maps to Files.App.Utils.Storage.Helpers.IStorageItemWithPath.
    """
    path: str
    item_type: FilesystemItemType = FilesystemItemType.File

    @property
    def name(self) -> str:
        return Path(self.path).name

    def exists(self) -> bool:
        return Path(self.path).exists()


@dataclass
class StorageHistory:
    """
    A single history entry for undo/redo.
    Maps to Files.App.Utils.Storage.History.IStorageHistory / StorageHistory.
    """
    operation_type: FileOperationType
    source: list[StorageItemWithPath] = field(default_factory=list)
    destination: list[StorageItemWithPath] = field(default_factory=list)
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.now)

    def modify(
        self,
        operation_type: FileOperationType,
        source: list[StorageItemWithPath],
        destination: list[StorageItemWithPath],
    ) -> None:
        self.operation_type = operation_type
        self.source = source
        self.destination = destination

    def __repr__(self) -> str:
        srcs = [s.path for s in self.source]
        dsts = [d.path for d in self.destination]
        return f"StorageHistory({self.operation_type.name} {srcs} → {dsts})"


# ===========================================================================
# Low-level filesystem operations  (IFilesystemOperations)
# ===========================================================================

class IFilesystemOperations(ABC):
    """
    Abstract low-level filesystem operations.
    Maps to Files.App.Utils.Storage.Operations.IFilesystemOperations.

    Every method returns a StorageHistory entry (not yet saved).
    The caller (FilesystemHelpers) decides whether to register history.
    """

    @abstractmethod
    def create(
        self,
        source: StorageItemWithPath,
        progress: Optional[Callable[[int], None]] = None,
    ) -> tuple[StorageHistory, Optional[str]]:
        """Create a file or folder. Returns (history, created_path)."""

    @abstractmethod
    def copy(
        self,
        source: StorageItemWithPath,
        destination: str,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """Copy source to destination path."""

    @abstractmethod
    def copy_items(
        self,
        sources: list[StorageItemWithPath],
        destinations: list[str],
        collisions: list[NameCollisionOption],
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """Copy multiple items."""

    @abstractmethod
    def move(
        self,
        source: StorageItemWithPath,
        destination: str,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """Move source to destination path."""

    @abstractmethod
    def move_items(
        self,
        sources: list[StorageItemWithPath],
        destinations: list[str],
        collisions: list[NameCollisionOption],
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """Move multiple items."""

    @abstractmethod
    def delete(
        self,
        source: StorageItemWithPath,
        permanently: bool = False,
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """Delete (or recycle) source."""

    @abstractmethod
    def delete_items(
        self,
        sources: list[StorageItemWithPath],
        permanently: bool = False,
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """Delete multiple items."""

    @abstractmethod
    def rename(
        self,
        source: StorageItemWithPath,
        new_name: str,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """Rename source to new_name (within same parent folder)."""

    @abstractmethod
    def restore_from_trash(
        self,
        source: StorageItemWithPath,
        destination: str,
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """Restore a recycled item back to destination."""

    @abstractmethod
    def create_shortcut_items(
        self,
        sources: list[StorageItemWithPath],
        destinations: list[str],
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """Create symbolic links / shortcuts."""


# ===========================================================================
# Concrete low-level implementation
# ===========================================================================

class FilesystemOperations(IFilesystemOperations):
    """
    Concrete filesystem operations using Python stdlib (shutil / os / pathlib).
    Maps to Files.App.Utils.Storage.Operations.FilesystemOperations +
    ShellFilesystemOperations (merged for simplicity).
    """

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _resolve_destination(self, dest: str, collision: NameCollisionOption) -> str:
        """
        Resolve the actual destination path respecting collision policy.
        Maps to the internal collision-resolution logic in FilesystemOperations.
        """
        dest_path = Path(dest)
        if not dest_path.exists():
            return dest

        if collision is NameCollisionOption.ReplaceExisting:
            return dest

        if collision is NameCollisionOption.FailIfExists:
            raise FileExistsError(f"Destination already exists: {dest}")

        # GenerateUniqueName
        stem = dest_path.stem
        suffix = dest_path.suffix
        parent = dest_path.parent
        counter = 1
        while True:
            candidate = parent / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return str(candidate)
            counter += 1

    def _report(self, progress: Optional[Callable[[int], None]], pct: int) -> None:
        if progress:
            try:
                progress(pct)
            except Exception:
                pass

    def _item_type(self, path: str) -> FilesystemItemType:
        p = Path(path)
        if p.is_symlink():
            return FilesystemItemType.Symlink
        if p.is_dir():
            return FilesystemItemType.Directory
        return FilesystemItemType.File

    def _make_history(
        self,
        op: FileOperationType,
        src_paths: list[str],
        dst_paths: list[str],
        src_type: FilesystemItemType = FilesystemItemType.File,
        dst_type: FilesystemItemType = FilesystemItemType.File,
    ) -> StorageHistory:
        return StorageHistory(
            operation_type=op,
            source=[StorageItemWithPath(p, src_type) for p in src_paths],
            destination=[StorageItemWithPath(p, dst_type) for p in dst_paths],
        )

    # ------------------------------------------------------------------ #
    # Create
    # ------------------------------------------------------------------ #

    def create(
        self,
        source: StorageItemWithPath,
        progress: Optional[Callable[[int], None]] = None,
    ) -> tuple[StorageHistory, Optional[str]]:
        """
        Create a file or directory.
        Maps to FilesystemOperations.CreateAsync.
        """
        self._report(progress, 0)
        p = Path(source.path)
        try:
            if source.item_type is FilesystemItemType.Directory:
                p.mkdir(parents=True, exist_ok=False)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.touch(exist_ok=False)
            self._report(progress, 100)
            log.info("Created: %s", source.path)
            history = self._make_history(FileOperationType.CreateNew, [source.path], [], source.item_type)
            return history, source.path
        except FileExistsError:
            log.warning("Create failed — already exists: %s", source.path)
            return self._make_history(FileOperationType.CreateNew, [], []), None
        except OSError as exc:
            log.error("Create failed: %s — %s", source.path, exc)
            return self._make_history(FileOperationType.CreateNew, [], []), None

    # ------------------------------------------------------------------ #
    # Copy
    # ------------------------------------------------------------------ #

    def copy(
        self,
        source: StorageItemWithPath,
        destination: str,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """
        Copy a single item to destination.
        Maps to FilesystemOperations.CopyAsync.
        """
        self._report(progress, 0)
        src_path = Path(source.path)
        try:
            actual_dest = self._resolve_destination(destination, collision)
            if src_path.is_dir():
                shutil.copytree(src_path, actual_dest)
            else:
                Path(actual_dest).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, actual_dest)
            self._report(progress, 100)
            log.info("Copied: %s → %s", source.path, actual_dest)
            return self._make_history(
                FileOperationType.Copy,
                [source.path], [actual_dest],
                self._item_type(source.path), self._item_type(actual_dest),
            )
        except Exception as exc:
            log.error("Copy failed: %s → %s — %s", source.path, destination, exc)
            return self._make_history(FileOperationType.Copy, [], [])

    def copy_items(
        self,
        sources: list[StorageItemWithPath],
        destinations: list[str],
        collisions: list[NameCollisionOption],
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """
        Copy multiple items.
        Maps to FilesystemOperations.CopyItemsAsync.
        """
        src_paths, dst_paths = [], []
        total = len(sources)
        for i, (src, dst, col) in enumerate(zip(sources, destinations, collisions)):
            hist = self.copy(src, dst, col, progress=None)
            if hist.destination:
                src_paths.append(hist.source[0].path)
                dst_paths.append(hist.destination[0].path)
            self._report(progress, int((i + 1) / total * 100))
        return self._make_history(FileOperationType.Copy, src_paths, dst_paths)

    # ------------------------------------------------------------------ #
    # Move
    # ------------------------------------------------------------------ #

    def move(
        self,
        source: StorageItemWithPath,
        destination: str,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """
        Move a single item to destination.
        Maps to FilesystemOperations.MoveAsync.
        """
        self._report(progress, 0)
        src_path = Path(source.path)
        original = source.path
        try:
            actual_dest = self._resolve_destination(destination, collision)
            Path(actual_dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), actual_dest)
            self._report(progress, 100)
            log.info("Moved: %s → %s", original, actual_dest)
            return self._make_history(FileOperationType.Move, [original], [actual_dest])
        except Exception as exc:
            log.error("Move failed: %s → %s — %s", original, destination, exc)
            return self._make_history(FileOperationType.Move, [], [])

    def move_items(
        self,
        sources: list[StorageItemWithPath],
        destinations: list[str],
        collisions: list[NameCollisionOption],
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """
        Move multiple items.
        Maps to FilesystemOperations.MoveItemsAsync.
        """
        src_paths, dst_paths = [], []
        total = len(sources)
        for i, (src, dst, col) in enumerate(zip(sources, destinations, collisions)):
            hist = self.move(src, dst, col, progress=None)
            if hist.destination:
                src_paths.append(hist.source[0].path)
                dst_paths.append(hist.destination[0].path)
            self._report(progress, int((i + 1) / total * 100))
        return self._make_history(FileOperationType.Move, src_paths, dst_paths)

    # ------------------------------------------------------------------ #
    # Delete
    # ------------------------------------------------------------------ #

    def delete(
        self,
        source: StorageItemWithPath,
        permanently: bool = False,
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """
        Delete or recycle an item.
        Maps to FilesystemOperations.DeleteAsync + StorageTrashBinService logic.
        On macOS/Linux uses send2trash when available; falls back to permanent delete.
        """
        self._report(progress, 0)
        src_path = Path(source.path)
        original = source.path
        recycled_path: str = ""

        try:
            if permanently:
                _remove_path(src_path)
                op = FileOperationType.Delete
            else:
                recycled_path = _send_to_trash(src_path)
                op = FileOperationType.Recycle
            self._report(progress, 100)
            log.info("%s: %s", op.name, original)
            return self._make_history(op, [original], [recycled_path] if recycled_path else [])
        except Exception as exc:
            log.error("Delete failed: %s — %s", original, exc)
            return self._make_history(FileOperationType.Delete, [], [])

    def delete_items(
        self,
        sources: list[StorageItemWithPath],
        permanently: bool = False,
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """
        Delete multiple items.
        Maps to FilesystemOperations.DeleteItemsAsync.
        """
        src_paths, dst_paths = [], []
        total = len(sources)
        for i, src in enumerate(sources):
            hist = self.delete(src, permanently, progress=None)
            if hist.source:
                src_paths.append(hist.source[0].path)
                dst_paths.extend(d.path for d in hist.destination)
            self._report(progress, int((i + 1) / total * 100))
        op = FileOperationType.Delete if permanently else FileOperationType.Recycle
        return self._make_history(op, src_paths, dst_paths)

    # ------------------------------------------------------------------ #
    # Rename
    # ------------------------------------------------------------------ #

    def rename(
        self,
        source: StorageItemWithPath,
        new_name: str,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """
        Rename item within its parent directory.
        Maps to FilesystemOperations.RenameAsync.
        """
        self._report(progress, 0)
        src_path = Path(source.path)
        new_path = src_path.parent / new_name
        original = source.path
        try:
            actual_dest = self._resolve_destination(str(new_path), collision)
            src_path.rename(actual_dest)
            self._report(progress, 100)
            log.info("Renamed: %s → %s", original, actual_dest)
            return self._make_history(FileOperationType.Rename, [original], [actual_dest])
        except Exception as exc:
            log.error("Rename failed: %s → %s — %s", original, new_name, exc)
            return self._make_history(FileOperationType.Rename, [], [])

    # ------------------------------------------------------------------ #
    # Restore from trash
    # ------------------------------------------------------------------ #

    def restore_from_trash(
        self,
        source: StorageItemWithPath,
        destination: str,
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """
        Restore an item from the Recycle Bin / Trash.
        Maps to FilesystemOperations.RestoreFromTrashAsync.
        Note: full shell-level restore (with metadata) is Windows-specific;
        this implementation moves the file from its trash location.
        """
        self._report(progress, 0)
        try:
            shutil.move(source.path, destination)
            self._report(progress, 100)
            log.info("Restored from trash: %s → %s", source.path, destination)
            return self._make_history(FileOperationType.Restore, [source.path], [destination])
        except Exception as exc:
            log.error("Restore failed: %s → %s — %s", source.path, destination, exc)
            return self._make_history(FileOperationType.Restore, [], [])

    # ------------------------------------------------------------------ #
    # Create shortcuts / symlinks
    # ------------------------------------------------------------------ #

    def create_shortcut_items(
        self,
        sources: list[StorageItemWithPath],
        destinations: list[str],
        progress: Optional[Callable[[int], None]] = None,
    ) -> StorageHistory:
        """
        Create symlinks (Unix) or .lnk shortcuts (Windows stub).
        Maps to FilesystemOperations.CreateShortcutItemsAsync.
        """
        src_paths, dst_paths = [], []
        total = len(sources)
        for i, (src, dst) in enumerate(zip(sources, destinations)):
            try:
                Path(dst).parent.mkdir(parents=True, exist_ok=True)
                os.symlink(src.path, dst)
                src_paths.append(src.path)
                dst_paths.append(dst)
                log.info("Shortcut created: %s → %s", dst, src.path)
            except Exception as exc:
                log.error("Shortcut failed: %s → %s — %s", src.path, dst, exc)
            self._report(progress, int((i + 1) / total * 100))
        return self._make_history(FileOperationType.CreateLink, src_paths, dst_paths)


# ===========================================================================
# Trash-bin helpers  (StorageTrashBinService)
# ===========================================================================

class TrashBinService:
    """
    Query the system Recycle Bin / Trash.
    Maps to Files.App.Services.Storage.StorageTrashBinService.
    """

    def has_items(self) -> bool:
        """Return True if the trash contains any items."""
        trash = _trash_dir()
        if not trash or not trash.exists():
            return False
        try:
            return any(True for _ in trash.iterdir())
        except PermissionError:
            return False

    def get_size(self) -> int:
        """Return total bytes consumed by trashed items."""
        trash = _trash_dir()
        if not trash or not trash.exists():
            return 0
        total = 0
        for p in trash.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
        return total

    def is_under_trash(self, path: str) -> bool:
        """Return True if path is inside the trash directory."""
        trash = _trash_dir()
        if not trash:
            return False
        try:
            Path(path).relative_to(trash)
            return True
        except ValueError:
            return False

    def empty_trash(self) -> ReturnResult:
        """Empty the entire Recycle Bin / Trash."""
        trash = _trash_dir()
        if not trash or not trash.exists():
            return ReturnResult.Success
        errors = 0
        for item in list(trash.iterdir()):
            try:
                _remove_path(item)
            except Exception as exc:
                log.error("Could not remove trash item %s: %s", item, exc)
                errors += 1
        return ReturnResult.Success if errors == 0 else ReturnResult.Failed


# ===========================================================================
# StorageArchiveService  (archive operations)
# ===========================================================================

class StorageArchiveService:
    """
    Archive compression/extraction.
    Maps to Files.App.Services.Storage.StorageArchiveService.
    """

    def compress(
        self,
        sources: list[str],
        output_path: str,
        format: str = "zip",
        progress: Optional[Callable[[int], None]] = None,
    ) -> FilesystemResult:
        """
        Compress sources into an archive.
        Supported formats: zip, tar, gztar, bztar, xztar.
        Maps to StorageArchiveService.CompressAsync.
        """
        try:
            base = str(Path(output_path).with_suffix(""))
            # shutil.make_archive accepts a single root dir; for multiple
            # sources we collect them into a temp staging area first.
            if len(sources) == 1 and Path(sources[0]).is_dir():
                shutil.make_archive(base, format, root_dir=str(Path(sources[0]).parent), base_dir=Path(sources[0]).name)
            else:
                import tempfile
                with tempfile.TemporaryDirectory() as stage:
                    for src in sources:
                        dst = Path(stage) / Path(src).name
                        if Path(src).is_dir():
                            shutil.copytree(src, dst)
                        else:
                            shutil.copy2(src, dst)
                    shutil.make_archive(base, format, root_dir=stage)
            if progress:
                progress(100)
            log.info("Compressed %d item(s) → %s", len(sources), output_path)
            return FilesystemResult.ok(output_path)
        except Exception as exc:
            log.error("Compress failed: %s", exc)
            return FilesystemResult.fail()

    def extract(
        self,
        archive_path: str,
        destination: str,
        progress: Optional[Callable[[int], None]] = None,
    ) -> FilesystemResult:
        """
        Extract an archive to destination.
        Maps to StorageArchiveService.DecompressAsync.
        """
        try:
            Path(destination).mkdir(parents=True, exist_ok=True)
            shutil.unpack_archive(archive_path, destination)
            if progress:
                progress(100)
            log.info("Extracted: %s → %s", archive_path, destination)
            return FilesystemResult.ok(destination)
        except Exception as exc:
            log.error("Extract failed: %s", exc)
            return FilesystemResult.fail()


# ===========================================================================
# StorageHistoryOperations  (undo / redo)
# ===========================================================================

class StorageHistoryOperations:
    """
    Execute undo and redo on StorageHistory entries.
    Maps to Files.App.Utils.Storage.History.StorageHistoryOperations.
    """

    def __init__(self, operations: FilesystemOperations):
        self._ops = operations

    def undo(self, history: StorageHistory) -> ReturnResult:
        """
        Reverse a recorded operation.
        Maps to StorageHistoryOperations.Undo().
        """
        op = history.operation_type
        try:
            if op is FileOperationType.CreateNew:
                # Opposite: delete the created items
                for s in history.source:
                    self._ops.delete(s, permanently=True)

            elif op is FileOperationType.CreateLink:
                for d in history.destination:
                    _remove_path(Path(d.path))

            elif op is FileOperationType.Rename:
                for src, dst in zip(history.source, history.destination):
                    self._ops.rename(dst, Path(src.path).name)

            elif op is FileOperationType.Copy:
                for d in history.destination:
                    self._ops.delete(d, permanently=True)

            elif op is FileOperationType.Move:
                for src, dst in zip(history.source, history.destination):
                    self._ops.move(dst, src.path, NameCollisionOption.ReplaceExisting)

            elif op is FileOperationType.Delete:
                log.warning("Cannot undo permanent delete for: %s", [s.path for s in history.source])
                return ReturnResult.Failed

            elif op is FileOperationType.Recycle:
                for src, dst in zip(history.source, history.destination):
                    if dst.path:
                        self._ops.restore_from_trash(dst, src.path)

            elif op is FileOperationType.Restore:
                for src in history.source:
                    self._ops.delete(src, permanently=False)

            return ReturnResult.Success
        except Exception as exc:
            log.error("Undo failed: %s", exc)
            return ReturnResult.Failed

    def redo(self, history: StorageHistory) -> ReturnResult:
        """
        Re-execute a recorded operation.
        Maps to StorageHistoryOperations.Redo().
        """
        op = history.operation_type
        try:
            if op is FileOperationType.CreateNew:
                for s in history.source:
                    self._ops.create(s)

            elif op is FileOperationType.CreateLink:
                srcs = history.source
                dsts = [d.path for d in history.destination]
                self._ops.create_shortcut_items(srcs, dsts)

            elif op is FileOperationType.Rename:
                for src, dst in zip(history.source, history.destination):
                    self._ops.rename(src, Path(dst.path).name)

            elif op is FileOperationType.Copy:
                for src, dst in zip(history.source, history.destination):
                    self._ops.copy(src, dst.path, NameCollisionOption.GenerateUniqueName)

            elif op is FileOperationType.Move:
                for src, dst in zip(history.source, history.destination):
                    self._ops.move(src, dst.path, NameCollisionOption.GenerateUniqueName)

            elif op in (FileOperationType.Delete, FileOperationType.Recycle):
                permanently = op is FileOperationType.Delete
                self._ops.delete_items(history.source, permanently=permanently)

            elif op is FileOperationType.Restore:
                for src, dst in zip(history.source, history.destination):
                    self._ops.restore_from_trash(src, dst.path)

            return ReturnResult.Success
        except Exception as exc:
            log.error("Redo failed: %s", exc)
            return ReturnResult.Failed


# ===========================================================================
# High-level helpers  (IFilesystemHelpers / FilesystemHelpers)
# ===========================================================================

class FilesystemHelpers:
    """
    High-level coordinator that wraps FilesystemOperations with:
      - History registration (undo/redo stack)
      - Confirmation callbacks
      - Batch operation support

    Maps to Files.App.Utils.Storage.Operations.FilesystemHelpers.
    """

    def __init__(
        self,
        operations: Optional[FilesystemOperations] = None,
        confirm_callback: Optional[Callable[[str], bool]] = None,
        history_limit: int = 64,
    ):
        self._ops = operations or FilesystemOperations()
        self._history_ops = StorageHistoryOperations(self._ops)
        self._undo_stack: list[StorageHistory] = []
        self._redo_stack: list[StorageHistory] = []
        self._history_limit = history_limit
        # confirm_callback(message) → True to proceed, False to cancel
        self._confirm = confirm_callback or (lambda msg: True)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _register(self, history: StorageHistory) -> None:
        if history.source or history.destination:
            self._undo_stack.append(history)
            if len(self._undo_stack) > self._history_limit:
                self._undo_stack.pop(0)
            self._redo_stack.clear()

    def _confirm_delete(self, sources: list[StorageItemWithPath], permanently: bool, policy: DeleteConfirmationPolicy) -> bool:
        if policy is DeleteConfirmationPolicy.Never:
            return True
        if policy is DeleteConfirmationPolicy.PermanentOnly and not permanently:
            return True
        names = ", ".join(s.name for s in sources[:3])
        suffix = f" and {len(sources) - 3} more" if len(sources) > 3 else ""
        perm_note = " permanently" if permanently else ""
        return self._confirm(f"Delete{perm_note}: {names}{suffix}?")

    # ------------------------------------------------------------------ #
    # Create
    # ------------------------------------------------------------------ #

    def create(
        self,
        source: StorageItemWithPath,
        register_history: bool = True,
    ) -> tuple[ReturnResult, Optional[str]]:
        """
        Create a file or folder.
        Maps to FilesystemHelpers.CreateAsync.
        """
        hist, created = self._ops.create(source)
        if created and register_history:
            self._register(hist)
        return (ReturnResult.Success if created else ReturnResult.Failed, created)

    # ------------------------------------------------------------------ #
    # Copy
    # ------------------------------------------------------------------ #

    def copy_item(
        self,
        source: StorageItemWithPath,
        destination: str,
        show_dialog: bool = False,
        register_history: bool = True,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
    ) -> ReturnResult:
        """
        Copy a single item.
        Maps to FilesystemHelpers.CopyItemAsync.
        """
        hist = self._ops.copy(source, destination, collision)
        ok = bool(hist.destination)
        if ok and register_history:
            self._register(hist)
        return ReturnResult.Success if ok else ReturnResult.Failed

    def copy_items(
        self,
        sources: list[StorageItemWithPath],
        destinations: list[str],
        show_dialog: bool = False,
        register_history: bool = True,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
    ) -> ReturnResult:
        """
        Copy multiple items.
        Maps to FilesystemHelpers.CopyItemsAsync.
        """
        collisions = [collision] * len(sources)
        hist = self._ops.copy_items(sources, destinations, collisions)
        ok = bool(hist.destination)
        if ok and register_history:
            self._register(hist)
        return ReturnResult.Success if ok else ReturnResult.Failed

    # ------------------------------------------------------------------ #
    # Move
    # ------------------------------------------------------------------ #

    def move_item(
        self,
        source: StorageItemWithPath,
        destination: str,
        show_dialog: bool = False,
        register_history: bool = True,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
    ) -> ReturnResult:
        """
        Move a single item.
        Maps to FilesystemHelpers.MoveItemAsync.
        """
        hist = self._ops.move(source, destination, collision)
        ok = bool(hist.destination)
        if ok and register_history:
            self._register(hist)
        return ReturnResult.Success if ok else ReturnResult.Failed

    def move_items(
        self,
        sources: list[StorageItemWithPath],
        destinations: list[str],
        show_dialog: bool = False,
        register_history: bool = True,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
    ) -> ReturnResult:
        """
        Move multiple items.
        Maps to FilesystemHelpers.MoveItemsAsync.
        """
        collisions = [collision] * len(sources)
        hist = self._ops.move_items(sources, destinations, collisions)
        ok = bool(hist.destination)
        if ok and register_history:
            self._register(hist)
        return ReturnResult.Success if ok else ReturnResult.Failed

    # ------------------------------------------------------------------ #
    # Delete
    # ------------------------------------------------------------------ #

    def delete_item(
        self,
        source: StorageItemWithPath,
        policy: DeleteConfirmationPolicy = DeleteConfirmationPolicy.PermanentOnly,
        permanently: bool = False,
        register_history: bool = True,
    ) -> ReturnResult:
        """
        Delete a single item.
        Maps to FilesystemHelpers.DeleteItemAsync.
        """
        return self.delete_items([source], policy, permanently, register_history)

    def delete_items(
        self,
        sources: list[StorageItemWithPath],
        policy: DeleteConfirmationPolicy = DeleteConfirmationPolicy.PermanentOnly,
        permanently: bool = False,
        register_history: bool = True,
    ) -> ReturnResult:
        """
        Delete multiple items.
        Maps to FilesystemHelpers.DeleteItemsAsync.
        """
        if not self._confirm_delete(sources, permanently, policy):
            return ReturnResult.Cancelled
        hist = self._ops.delete_items(sources, permanently)
        ok = bool(hist.source)
        if ok and register_history:
            self._register(hist)
        return ReturnResult.Success if ok else ReturnResult.Failed

    # ------------------------------------------------------------------ #
    # Rename
    # ------------------------------------------------------------------ #

    def rename(
        self,
        source: StorageItemWithPath,
        new_name: str,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
        register_history: bool = True,
    ) -> ReturnResult:
        """
        Rename an item.
        Maps to FilesystemHelpers.RenameAsync.
        """
        hist = self._ops.rename(source, new_name, collision)
        ok = bool(hist.destination)
        if ok and register_history:
            self._register(hist)
        return ReturnResult.Success if ok else ReturnResult.Failed

    # ------------------------------------------------------------------ #
    # Restore from trash
    # ------------------------------------------------------------------ #

    def restore_item_from_trash(
        self,
        source: StorageItemWithPath,
        destination: str,
        register_history: bool = True,
    ) -> ReturnResult:
        """
        Restore a recycled item.
        Maps to FilesystemHelpers.RestoreItemFromTrashAsync.
        """
        hist = self._ops.restore_from_trash(source, destination)
        ok = bool(hist.destination)
        if ok and register_history:
            self._register(hist)
        return ReturnResult.Success if ok else ReturnResult.Failed

    # ------------------------------------------------------------------ #
    # Shortcuts
    # ------------------------------------------------------------------ #

    def create_shortcut(
        self,
        sources: list[StorageItemWithPath],
        destinations: list[str],
        register_history: bool = True,
    ) -> ReturnResult:
        """
        Create symlinks / shortcuts.
        Maps to FilesystemHelpers (CreateShortcutFromClipboard path).
        """
        hist = self._ops.create_shortcut_items(sources, destinations)
        ok = bool(hist.destination)
        if ok and register_history:
            self._register(hist)
        return ReturnResult.Success if ok else ReturnResult.Failed

    # ------------------------------------------------------------------ #
    # Undo / Redo
    # ------------------------------------------------------------------ #

    def undo(self) -> ReturnResult:
        """
        Undo the last registered operation.
        Maps to StorageHistoryOperations.Undo().
        """
        if not self._undo_stack:
            log.info("Nothing to undo.")
            return ReturnResult.Failed
        history = self._undo_stack.pop()
        result = self._history_ops.undo(history)
        if result is ReturnResult.Success:
            self._redo_stack.append(history)
        return result

    def redo(self) -> ReturnResult:
        """
        Redo the last undone operation.
        Maps to StorageHistoryOperations.Redo().
        """
        if not self._redo_stack:
            log.info("Nothing to redo.")
            return ReturnResult.Failed
        history = self._redo_stack.pop()
        result = self._history_ops.redo(history)
        if result is ReturnResult.Success:
            self._undo_stack.append(history)
        return result

    @property
    def undo_history(self) -> list[StorageHistory]:
        """Read-only view of the undo stack."""
        return list(self._undo_stack)

    @property
    def redo_history(self) -> list[StorageHistory]:
        """Read-only view of the redo stack."""
        return list(self._redo_stack)


# ===========================================================================
# Storage properties / info helpers  (FilePropertiesHelpers / DriveHelpers)
# ===========================================================================

@dataclass
class ItemProperties:
    """
    File/folder metadata snapshot.
    Maps to Files.App.ViewModels.Properties.Items.FileProperties + FolderProperties.
    """
    path: str
    name: str
    size_bytes: int
    item_type: FilesystemItemType
    created: datetime.datetime
    modified: datetime.datetime
    is_readonly: bool
    is_hidden: bool
    checksum_md5: Optional[str] = None

    def __str__(self) -> str:
        return (
            f"{self.name} | {self.item_type.name} | "
            f"{self.size_bytes:,} bytes | modified {self.modified.isoformat()}"
        )


def get_item_properties(path: str, compute_checksum: bool = False) -> Optional[ItemProperties]:
    """
    Retrieve metadata for a file or folder.
    Maps to Files.App.Utils.Storage.Helpers.FilePropertiesHelpers.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        st = p.stat()
        is_hidden = p.name.startswith(".") if platform.system() != "Windows" else bool(st.st_file_attributes & 0x2 if hasattr(st, "st_file_attributes") else 0)  # type: ignore[attr-defined]
        is_readonly = not os.access(path, os.W_OK)
        size = _dir_size(p) if p.is_dir() else st.st_size
        md5 = None
        if compute_checksum and p.is_file():
            md5 = _md5(p)
        return ItemProperties(
            path=str(p),
            name=p.name,
            size_bytes=size,
            item_type=FilesystemItemType.Directory if p.is_dir() else FilesystemItemType.File,
            created=datetime.datetime.fromtimestamp(st.st_ctime),
            modified=datetime.datetime.fromtimestamp(st.st_mtime),
            is_readonly=is_readonly,
            is_hidden=is_hidden,
            checksum_md5=md5,
        )
    except OSError as exc:
        log.error("get_item_properties failed for %s: %s", path, exc)
        return None


@dataclass
class DriveInfo:
    """
    Drive / volume information.
    Maps to Files.App.Data.Items.DriveItem + DriveHelpers.
    """
    path: str
    name: str
    total_bytes: int
    free_bytes: int
    drive_type: str  # "Fixed", "Removable", "Network", "CDRom", "Ram", "Unknown"

    @property
    def used_bytes(self) -> int:
        return self.total_bytes - self.free_bytes

    @property
    def usage_pct(self) -> float:
        return (self.used_bytes / self.total_bytes * 100) if self.total_bytes else 0.0


def get_drives() -> list[DriveInfo]:
    """
    Enumerate mounted drives / volumes.
    Maps to Files.App.Utils.Storage.Helpers.DriveHelpers + DrivesViewModel.
    """
    drives = []
    try:
        import shutil as _shutil
        if platform.system() == "Windows":
            import string
            for letter in string.ascii_uppercase:
                root = f"{letter}:\\"
                if Path(root).exists():
                    usage = _shutil.disk_usage(root)
                    drives.append(DriveInfo(
                        path=root, name=root,
                        total_bytes=usage.total, free_bytes=usage.free,
                        drive_type="Fixed",
                    ))
        else:
            # Linux / macOS: read /proc/mounts or use shutil on /
            candidates = ["/"] + [str(p) for p in Path("/Volumes").iterdir()] if Path("/Volumes").exists() else ["/"]
            seen: set[str] = set()
            for mount in candidates:
                try:
                    usage = _shutil.disk_usage(mount)
                    key = str(usage.total)
                    if key in seen:
                        continue
                    seen.add(key)
                    drives.append(DriveInfo(
                        path=mount, name=Path(mount).name or mount,
                        total_bytes=usage.total, free_bytes=usage.free,
                        drive_type="Fixed",
                    ))
                except OSError:
                    pass
    except Exception as exc:
        log.error("get_drives failed: %s", exc)
    return drives


# ===========================================================================
# FolderSearch  (Utils/Storage/Search/FolderSearch.cs)
# ===========================================================================

def search_folder(
    root: str,
    query: str,
    recursive: bool = True,
    match_case: bool = False,
    include_files: bool = True,
    include_folders: bool = True,
    max_results: int = 500,
) -> list[str]:
    """
    Search for files/folders matching query under root.
    Maps to Files.App.Utils.Storage.Search.FolderSearch.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return []

    results: list[str] = []
    needle = query if match_case else query.lower()
    glob = root_path.rglob("*") if recursive else root_path.glob("*")

    for item in glob:
        if len(results) >= max_results:
            break
        name = item.name if match_case else item.name.lower()
        if needle not in name:
            continue
        if item.is_file() and not include_files:
            continue
        if item.is_dir() and not include_folders:
            continue
        results.append(str(item))

    return results


# ===========================================================================
# FileSizeCalculator  (Utils/Storage/Operations/FileSizeCalculator.cs)
# ===========================================================================

def calculate_folder_size(path: str) -> int:
    """
    Recursively compute directory size in bytes.
    Maps to Files.App.Utils.Storage.Operations.FileSizeCalculator.
    """
    return _dir_size(Path(path))


# ===========================================================================
# Top-level FilesystemManager  (the public entrypoint)
# ===========================================================================

class FilesystemManager:
    """
    Unified public API that composes all subsystems:
      - FilesystemHelpers   (high-level operations + undo/redo)
      - TrashBinService     (recycle bin queries)
      - StorageArchiveService (compression)
      - Convenience wrappers for properties, drives, search, sizes

    Usage:
        fs = create_filesystem()
        fs.copy("a.txt", "b.txt")
        fs.undo()
    """

    def __init__(
        self,
        confirm_callback: Optional[Callable[[str], bool]] = None,
        history_limit: int = 64,
    ):
        ops = FilesystemOperations()
        self.helpers = FilesystemHelpers(ops, confirm_callback, history_limit)
        self.trash = TrashBinService()
        self.archives = StorageArchiveService()

    # ------------------------------------------------------------------ #
    # Item creation
    # ------------------------------------------------------------------ #

    def create_file(self, path: str, register_history: bool = True) -> tuple[ReturnResult, Optional[str]]:
        """Create an empty file."""
        return self.helpers.create(StorageItemWithPath(path, FilesystemItemType.File), register_history)

    def create_folder(self, path: str, register_history: bool = True) -> tuple[ReturnResult, Optional[str]]:
        """Create a directory (and parents)."""
        return self.helpers.create(StorageItemWithPath(path, FilesystemItemType.Directory), register_history)

    # ------------------------------------------------------------------ #
    # Copy
    # ------------------------------------------------------------------ #

    def copy(
        self,
        source: str,
        destination: str,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
        register_history: bool = True,
    ) -> ReturnResult:
        """Copy source to destination."""
        return self.helpers.copy_item(
            StorageItemWithPath(source),
            destination,
            register_history=register_history,
            collision=collision,
        )

    def copy_many(
        self,
        sources: list[str],
        destinations: list[str],
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
        register_history: bool = True,
    ) -> ReturnResult:
        """Copy multiple items."""
        return self.helpers.copy_items(
            [StorageItemWithPath(s) for s in sources],
            destinations,
            register_history=register_history,
            collision=collision,
        )

    # ------------------------------------------------------------------ #
    # Move
    # ------------------------------------------------------------------ #

    def move(
        self,
        source: str,
        destination: str,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
        register_history: bool = True,
    ) -> ReturnResult:
        """Move source to destination."""
        return self.helpers.move_item(
            StorageItemWithPath(source),
            destination,
            register_history=register_history,
            collision=collision,
        )

    def move_many(
        self,
        sources: list[str],
        destinations: list[str],
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
        register_history: bool = True,
    ) -> ReturnResult:
        """Move multiple items."""
        return self.helpers.move_items(
            [StorageItemWithPath(s) for s in sources],
            destinations,
            register_history=register_history,
            collision=collision,
        )

    # ------------------------------------------------------------------ #
    # Delete
    # ------------------------------------------------------------------ #

    def delete(
        self,
        path: str,
        permanently: bool = False,
        policy: DeleteConfirmationPolicy = DeleteConfirmationPolicy.PermanentOnly,
        register_history: bool = True,
    ) -> ReturnResult:
        """Delete or recycle an item."""
        return self.helpers.delete_item(
            StorageItemWithPath(path),
            policy=policy,
            permanently=permanently,
            register_history=register_history,
        )

    def delete_many(
        self,
        paths: list[str],
        permanently: bool = False,
        policy: DeleteConfirmationPolicy = DeleteConfirmationPolicy.PermanentOnly,
        register_history: bool = True,
    ) -> ReturnResult:
        """Delete or recycle multiple items."""
        return self.helpers.delete_items(
            [StorageItemWithPath(p) for p in paths],
            policy=policy,
            permanently=permanently,
            register_history=register_history,
        )

    # ------------------------------------------------------------------ #
    # Rename
    # ------------------------------------------------------------------ #

    def rename(
        self,
        path: str,
        new_name: str,
        collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName,
        register_history: bool = True,
    ) -> ReturnResult:
        """Rename a file or folder."""
        return self.helpers.rename(
            StorageItemWithPath(path),
            new_name,
            collision=collision,
            register_history=register_history,
        )

    # ------------------------------------------------------------------ #
    # Shortcuts
    # ------------------------------------------------------------------ #

    def create_shortcut(
        self,
        source: str,
        link_path: str,
        register_history: bool = True,
    ) -> ReturnResult:
        """Create a symbolic link from link_path → source."""
        return self.helpers.create_shortcut(
            [StorageItemWithPath(source)],
            [link_path],
            register_history=register_history,
        )

    # ------------------------------------------------------------------ #
    # Undo / Redo
    # ------------------------------------------------------------------ #

    def undo(self) -> ReturnResult:
        """Undo the last filesystem operation."""
        return self.helpers.undo()

    def redo(self) -> ReturnResult:
        """Redo the last undone operation."""
        return self.helpers.redo()

    @property
    def undo_history(self) -> list[StorageHistory]:
        return self.helpers.undo_history

    @property
    def redo_history(self) -> list[StorageHistory]:
        return self.helpers.redo_history

    # ------------------------------------------------------------------ #
    # Recycle Bin
    # ------------------------------------------------------------------ #

    def restore_from_trash(self, trash_path: str, destination: str, register_history: bool = True) -> ReturnResult:
        return self.helpers.restore_item_from_trash(StorageItemWithPath(trash_path), destination, register_history)

    def empty_trash(self) -> ReturnResult:
        return self.trash.empty_trash()

    def trash_has_items(self) -> bool:
        return self.trash.has_items()

    def trash_size(self) -> int:
        return self.trash.get_size()

    # ------------------------------------------------------------------ #
    # Archives
    # ------------------------------------------------------------------ #

    def compress(self, sources: list[str], output: str, format: str = "zip") -> FilesystemResult:
        return self.archives.compress(sources, output, format)

    def extract(self, archive: str, destination: str) -> FilesystemResult:
        return self.archives.extract(archive, destination)

    # ------------------------------------------------------------------ #
    # Properties / info
    # ------------------------------------------------------------------ #

    def get_properties(self, path: str, checksum: bool = False) -> Optional[ItemProperties]:
        return get_item_properties(path, checksum)

    def get_drives(self) -> list[DriveInfo]:
        return get_drives()

    def search(
        self,
        root: str,
        query: str,
        recursive: bool = True,
        max_results: int = 500,
    ) -> list[str]:
        return search_folder(root, query, recursive, max_results=max_results)

    def folder_size(self, path: str) -> int:
        return calculate_folder_size(path)


# ===========================================================================
# Module-level convenience functions  (importable entrypoints)
# ===========================================================================

def create_filesystem(
    confirm_callback: Optional[Callable[[str], bool]] = None,
    history_limit: int = 64,
) -> FilesystemManager:
    """
    Factory function — the primary entrypoint.

    Returns a fully configured FilesystemManager ready for use.

    Parameters
    ----------
    confirm_callback : callable(str) -> bool, optional
        Called before destructive operations with a human-readable message.
        Return True to proceed, False to cancel.
        If None, all operations are auto-confirmed.
    history_limit : int
        Maximum number of undo steps to retain.

    Example
    -------
        fs = create_filesystem()
        fs.copy("/tmp/a.txt", "/tmp/b.txt")
        fs.undo()
    """
    return FilesystemManager(confirm_callback=confirm_callback, history_limit=history_limit)


def copy_file(source: str, destination: str, collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName) -> ReturnResult:
    """Convenience: copy a single file without history tracking."""
    ops = FilesystemOperations()
    hist = ops.copy(StorageItemWithPath(source), destination, collision)
    return ReturnResult.Success if hist.destination else ReturnResult.Failed


def move_file(source: str, destination: str, collision: NameCollisionOption = NameCollisionOption.GenerateUniqueName) -> ReturnResult:
    """Convenience: move a single file without history tracking."""
    ops = FilesystemOperations()
    hist = ops.move(StorageItemWithPath(source), destination, collision)
    return ReturnResult.Success if hist.destination else ReturnResult.Failed


def delete_file(path: str, permanently: bool = False) -> ReturnResult:
    """Convenience: delete a single file/folder without history tracking."""
    ops = FilesystemOperations()
    hist = ops.delete(StorageItemWithPath(path), permanently)
    return ReturnResult.Success if hist.source else ReturnResult.Failed


def rename_file(path: str, new_name: str) -> ReturnResult:
    """Convenience: rename a file/folder without history tracking."""
    ops = FilesystemOperations()
    hist = ops.rename(StorageItemWithPath(path), new_name)
    return ReturnResult.Success if hist.destination else ReturnResult.Failed


def create_item(path: str, is_directory: bool = False) -> tuple[ReturnResult, Optional[str]]:
    """Convenience: create a file or folder without history tracking."""
    ops = FilesystemOperations()
    item_type = FilesystemItemType.Directory if is_directory else FilesystemItemType.File
    hist, created = ops.create(StorageItemWithPath(path, item_type))
    return (ReturnResult.Success if created else ReturnResult.Failed, created)


# ===========================================================================
# Private platform utilities
# ===========================================================================

def _remove_path(p: Path) -> None:
    """Remove a file or directory tree, force-unlocking read-only items."""
    if not p.exists() and not p.is_symlink():
        return
    if p.is_symlink() or p.is_file():
        try:
            p.unlink()
        except PermissionError:
            p.chmod(stat.S_IWRITE)
            p.unlink()
    else:
        shutil.rmtree(p, onerror=_handle_remove_error)


def _handle_remove_error(func, path, exc_info) -> None:
    """onerror handler for shutil.rmtree — force-chmod and retry."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _send_to_trash(p: Path) -> str:
    """
    Send a path to the system trash/recycle bin.
    Returns the trash path on success, or empty string if unsupported.
    """
    try:
        import send2trash  # type: ignore[import]
        send2trash.send2trash(str(p))
        return ""  # send2trash doesn't expose the trash path
    except ImportError:
        pass
    # Fallback: move to a local .trash directory
    trash = _trash_dir(create=True)
    if trash:
        dest = trash / p.name
        if dest.exists():
            dest = trash / f"{p.stem}_{int(datetime.datetime.now().timestamp())}{p.suffix}"
        shutil.move(str(p), str(dest))
        return str(dest)
    # Last resort: permanent delete
    _remove_path(p)
    return ""


def _trash_dir(create: bool = False) -> Optional[Path]:
    """Return (and optionally create) a platform-appropriate trash directory."""
    if platform.system() == "Windows":
        # On Windows the real Recycle Bin is opaque; use a local fallback
        trash = Path.home() / ".local_trash"
    elif platform.system() == "Darwin":
        trash = Path.home() / ".Trash"
    else:
        # XDG Trash
        xdg_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        trash = xdg_data / "Trash" / "files"
    if create:
        trash.mkdir(parents=True, exist_ok=True)
    return trash if trash.exists() else None


def _dir_size(p: Path) -> int:
    total = 0
    try:
        for item in p.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def _md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ===========================================================================
# CLI demo  (python filesystem.py --help)
# ===========================================================================

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Files App filesystem engine — CLI demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  copy SOURCE DEST        Copy SOURCE to DEST
  move SOURCE DEST        Move SOURCE to DEST
  delete PATH             Recycle PATH (use --permanent to skip recycle bin)
  rename PATH NEW_NAME    Rename PATH to NEW_NAME
  create PATH             Create a file at PATH (use --dir for a folder)
  info PATH               Show item properties
  search ROOT QUERY       Search ROOT for items matching QUERY
  drives                  List mounted drives
  trash-info              Show recycle bin status
  compress SRC DEST       Compress SRC into DEST archive
  extract ARCHIVE DEST    Extract ARCHIVE to DEST folder
        """,
    )
    parser.add_argument("command", help="Operation to perform")
    parser.add_argument("args", nargs="*", help="Arguments for the command")
    parser.add_argument("--permanent", action="store_true", help="Delete permanently (skip recycle bin)")
    parser.add_argument("--dir", action="store_true", help="Target is a directory")
    parser.add_argument("--format", default="zip", help="Archive format (default: zip)")
    parser.add_argument("--checksum", action="store_true", help="Compute MD5 checksum in info command")

    args = parser.parse_args()
    fs = create_filesystem(confirm_callback=lambda msg: (print(f"[confirm] {msg}") or True))

    cmd = args.command.lower()
    cmd_args = args.args

    if cmd == "copy" and len(cmd_args) == 2:
        r = fs.copy(cmd_args[0], cmd_args[1])
        print(r.name)

    elif cmd == "move" and len(cmd_args) == 2:
        r = fs.move(cmd_args[0], cmd_args[1])
        print(r.name)

    elif cmd == "delete" and len(cmd_args) == 1:
        r = fs.delete(cmd_args[0], permanently=args.permanent)
        print(r.name)

    elif cmd == "rename" and len(cmd_args) == 2:
        r = fs.rename(cmd_args[0], cmd_args[1])
        print(r.name)

    elif cmd == "create" and len(cmd_args) == 1:
        r, created = (fs.create_folder if args.dir else fs.create_file)(cmd_args[0])
        print(r.name, "→", created)

    elif cmd == "info" and len(cmd_args) == 1:
        props = fs.get_properties(cmd_args[0], checksum=args.checksum)
        if props:
            print(props)
            if props.checksum_md5:
                print(f"  MD5: {props.checksum_md5}")
        else:
            print("Not found.")

    elif cmd == "search" and len(cmd_args) == 2:
        results = fs.search(cmd_args[0], cmd_args[1])
        for r in results:
            print(r)
        print(f"({len(results)} results)")

    elif cmd == "drives":
        for d in fs.get_drives():
            pct = f"{d.usage_pct:.1f}%"
            print(f"  {d.path:20s} total={d.total_bytes:>15,}  free={d.free_bytes:>15,}  used={pct}")

    elif cmd == "trash-info":
        print(f"Has items : {fs.trash_has_items()}")
        print(f"Size      : {fs.trash_size():,} bytes")

    elif cmd == "compress" and len(cmd_args) == 2:
        res = fs.compress([cmd_args[0]], cmd_args[1], format=args.format)
        print("Success" if res else "Failed", "→", res.result)

    elif cmd == "extract" and len(cmd_args) == 2:
        res = fs.extract(cmd_args[0], cmd_args[1])
        print("Success" if res else "Failed", "→", res.result)

    else:
        parser.print_help()
        sys.exit(1)
