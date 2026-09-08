"""Central configuration access for Pandora."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "shared" / "config" / "configuration.json"


class ConfigurationError(RuntimeError):
    """Raised when the central configuration cannot be loaded or is invalid."""


def _read_configuration(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Configuration file not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Unable to read configuration: {path}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("The root of configuration.json must be a JSON object")
    return value


class CentralConfig:
    """Thread-safe, cached view of shared/config/configuration.json."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, Any] | None = None

    def reload(self) -> dict[str, Any]:
        with self._lock:
            self._data = _read_configuration(self.path)
            return dict(self._data)

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            if self._data is None:
                self.reload()
            return dict(self._data or {})

    def get(self, key: str, default: Any = None) -> Any:
        """Read a dotted path such as ``security.shellExecution``."""
        value: Any = self.as_dict()
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value


_CONFIG = CentralConfig()


def get_config(*, reload: bool = False) -> CentralConfig:
    if reload:
        _CONFIG.reload()
    else:
        _CONFIG.as_dict()
    return _CONFIG


def load_config(*, reload: bool = False) -> dict[str, Any]:
    return get_config(reload=reload).as_dict()
