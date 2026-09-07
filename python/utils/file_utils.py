"""
utils/file_utils.py
====================
Single shared-storage access point for every handler.

All persistent handler data (notes, tasks, memory, cache) lives under the
project-root `shared/` folder — never re-created per-module. Every handler
should call `load_json(...)` / `save_json(...)` from here instead of rolling
its own open()/json.load() pair.
"""

import json
from pathlib import Path
from typing import Any

# utils/ -> python/ -> project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent
SHARED_DIR = BASE_DIR / "shared"


def shared_path(*parts: str) -> Path:
    """Build a path rooted at the shared shared/ directory, e.g.
    shared_path("memory", "tasks", "tasks.json")."""
    return SHARED_DIR.joinpath(*parts)


def load_json(path: Path, default: Any = None) -> Any:
    """Load JSON from `path`, creating parent dirs and returning `default`
    (or {} ) if the file is missing, empty, or corrupt."""
    if default is None:
        default = {}
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            return default
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return default
            return json.loads(content)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, data: Any) -> bool:
    """Persist `data` as JSON to `path`, creating parent dirs as needed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        return True
    except OSError as exc:
        print(f"[file_utils] Failed to save {path}: {exc}")
        return False


def load_config() -> dict:
    """Load shared/config/configuration.json (API keys + settings)."""
    return load_json(shared_path("config", "configuration.json"), default={})


def get_api_key(name: str) -> str:
    """Look up a single API key from shared/config/configuration.json."""
    cfg = load_config()
    return cfg.get("api_keys", {}).get(name, "")
