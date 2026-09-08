"""Persistent cache supervisor for Pandora runtime responses.

The manager keeps metadata in ``shared/cache/cache.json`` and binary payloads
in ``shared/cache/objects``.  Callers use namespaced keys so STT, TTS, and
future response caches cannot collide.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
STORAGE_DIR = ROOT / "shared" / "cache"
INDEX_PATH = STORAGE_DIR / "cache.json"
OBJECTS_DIR = STORAGE_DIR / "objects"


class CacheManager:
    """Thread-safe JSON-indexed cache with TTL and binary-object support."""

    _instance: "CacheManager | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "CacheManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._lock = threading.RLock()
                cls._instance._ensure_storage()
        return cls._instance

    def _ensure_storage(self) -> None:
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        OBJECTS_DIR.mkdir(parents=True, exist_ok=True)
        if not INDEX_PATH.exists():
            self._write_index({})

    def _read_index(self) -> dict[str, dict[str, Any]]:
        try:
            with INDEX_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_index(self, data: dict[str, dict[str, Any]]) -> None:
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="cache-", suffix=".json", dir=STORAGE_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.replace(temp_name, INDEX_PATH)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def make_key(namespace: str, *parts: str | bytes) -> str:
        digest = hashlib.sha256()
        digest.update(namespace.encode("utf-8"))
        for part in parts:
            digest.update(b"\0")
            digest.update(part if isinstance(part, bytes) else part.encode("utf-8"))
        return f"{namespace}:{digest.hexdigest()}"

    def get(self, key: str) -> Any | None:
        with self._lock:
            index = self._read_index()
            entry = index.get(key)
            if not entry:
                return None
            expires_at = entry.get("expires_at")
            if expires_at is not None and expires_at <= time.time():
                self.delete(key)
                return None
            if entry.get("kind") == "bytes":
                try:
                    return (STORAGE_DIR / entry["value"]).read_bytes()
                except (KeyError, OSError):
                    self.delete(key)
                    return None
            return entry.get("value")

    def set(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        with self._lock:
            index = self._read_index()
            index[key] = {
                "kind": "json",
                "value": value,
                "created_at": time.time(),
                "expires_at": time.time() + ttl_seconds if ttl_seconds else None,
            }
            self._write_index(index)

    def set_bytes(self, key: str, value: bytes, ttl_seconds: float | None = None) -> None:
        self.set_bytes_with_metadata(key, value, ttl_seconds=ttl_seconds)

    def set_bytes_with_metadata(
        self,
        key: str,
        value: bytes,
        metadata: dict[str, str] | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        with self._lock:
            index = self._read_index()
            object_path = Path("objects") / f"{hashlib.sha256(key.encode()).hexdigest()}.bin"
            index[key] = {
                "kind": "bytes",
                "value": str(object_path),
                "sha256": hashlib.sha256(value).hexdigest(),
                "size": len(value),
                "metadata": metadata or {},
                "created_at": time.time(),
                "expires_at": time.time() + ttl_seconds if ttl_seconds else None,
            }
            (STORAGE_DIR / index[key]["value"]).write_bytes(value)
            self._write_index(index)

    def get_bytes(
        self,
        key: str,
        expected_metadata: dict[str, str] | None = None,
    ) -> bytes | None:
        """Return a binary entry only when its content and metadata are valid."""
        with self._lock:
            index = self._read_index()
            entry = index.get(key)
            if not entry or entry.get("kind") != "bytes":
                return None
            expires_at = entry.get("expires_at")
            if expires_at is not None and expires_at <= time.time():
                self.delete(key)
                return None
            if expected_metadata and entry.get("metadata") != expected_metadata:
                self.delete(key)
                return None
            try:
                value = (STORAGE_DIR / entry["value"]).read_bytes()
            except (KeyError, OSError):
                self.delete(key)
                return None
            if (
                entry.get("size") != len(value)
                or entry.get("sha256") != hashlib.sha256(value).hexdigest()
            ):
                self.delete(key)
                return None
            return value

    def delete(self, key: str) -> None:
        with self._lock:
            index = self._read_index()
            entry = index.get(key)
            if entry and entry.get("kind") == "bytes" and entry.get("value"):
                try:
                    (STORAGE_DIR / entry["value"]).unlink(missing_ok=True)
                except OSError:
                    pass
            if entry is not None:
                del index[key]
                self._write_index(index)

    def clear_expired(self) -> int:
        with self._lock:
            index = self._read_index()
            now = time.time()
            active = {}
            for key, entry in index.items():
                if entry.get("expires_at") is None or entry["expires_at"] > now:
                    active[key] = entry
                elif entry.get("kind") == "bytes" and entry.get("value"):
                    try:
                        (STORAGE_DIR / entry["value"]).unlink(missing_ok=True)
                    except OSError:
                        pass
            self._write_index(active)
            return len(index) - len(active)


cache = CacheManager()