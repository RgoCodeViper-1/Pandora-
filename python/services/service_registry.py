"""
services/service_registry.py
=============================
Lightweight service locator for the Pandora runtime.

Holds one singleton instance per service name.  No logic, no lifecycle —
just a shared dictionary with typed accessors.

Components register themselves at startup; consumers resolve by name.
This removes direct import coupling between pipeline services so that
e.g. VoiceRuntime does not need to import VADService directly.

Usage::

    # At startup (voice_runtime.py):
    registry = ServiceRegistry.instance()
    registry.register("whisper_manager", WhisperManager())
    registry.register("vad",             VADService(cfg))
    registry.register("stt",             SpeechToTextEngine())

    # Anywhere that needs the STT engine:
    stt = ServiceRegistry.instance().resolve("stt", SpeechToTextEngine)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Type, TypeVar

logger = logging.getLogger("pandora.registry")

T = TypeVar("T")


class ServiceRegistry:
    """Thread-safe singleton service locator."""

    _instance: Optional["ServiceRegistry"] = None

    def __init__(self) -> None:
        self._services: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "ServiceRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset for testing — clears the singleton."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Registration / resolution
    # ------------------------------------------------------------------

    def register(self, name: str, service: Any) -> None:
        """
        Register a service under ``name``.
        Re-registration replaces the previous entry and logs a warning.
        """
        if name in self._services:
            logger.warning(
                "ServiceRegistry: replacing existing service %r", name
            )
        self._services[name] = service
        logger.debug("ServiceRegistry: registered %r (%s)", name, type(service).__name__)

    def resolve(self, name: str, expected_type: Type[T] | None = None) -> T:
        """
        Return the service registered under ``name``.

        Raises ``KeyError`` if the service is not registered.
        Raises ``TypeError`` if ``expected_type`` is given and the instance
        does not match.
        """
        if name not in self._services:
            raise KeyError(
                f"ServiceRegistry: no service registered as {name!r}. "
                f"Registered: {list(self._services)}"
            )
        svc = self._services[name]
        if expected_type is not None and not isinstance(svc, expected_type):
            raise TypeError(
                f"ServiceRegistry: {name!r} is {type(svc).__name__}, "
                f"expected {expected_type.__name__}"
            )
        return svc  # type: ignore[return-value]

    def get(self, name: str, default: Any = None) -> Any:
        """Return the service or ``default`` when not registered."""
        return self._services.get(name, default)

    def is_registered(self, name: str) -> bool:
        return name in self._services

    def registered_names(self) -> list[str]:
        return list(self._services.keys())
