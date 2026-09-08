"""Low-resource, event-driven idle mode."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from typing import Any

logger = logging.getLogger("pandora.modes.optimized_idle")


class OptimizedIdle:
    """Run lightweight watchers and invoke heavy subsystems only on events."""

    DEFAULT_SERVICES = (
        "telemetry",
        "health",
        "resource_monitor",
        "security_monitor",
        "wakeword",
    )

    def __init__(
        self,
        service_registry: Any | None = None,
        *,
        background_services: Iterable[str] | None = None,
        on_invoke: Callable[[str, Any], Any] | None = None,
    ) -> None:
        self._registry = service_registry
        self._services = tuple(background_services or self.DEFAULT_SERVICES)
        self._on_invoke = on_invoke
        self._lock = threading.RLock()
        self._active = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def enter(self) -> None:
        with self._lock:
            if self._active:
                return
            self._active = True
        for name in self._services:
            service = self._resolve(name)
            if service is not None:
                self._call_lifecycle(service, "start", name)
        logger.info("Optimized Idle active; heavy subsystems remain stopped")

    def leave(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
        for name in reversed(self._services):
            service = self._resolve(name)
            if service is not None:
                self._call_lifecycle(service, "stop", name)

    def invoke(self, subsystem: str, payload: Any = None) -> Any:
        if not subsystem or subsystem in self._services:
            raise ValueError("A non-background subsystem name is required")
        if self._on_invoke is not None:
            return self._on_invoke(subsystem, payload)
        service = self._resolve(subsystem)
        if service is None:
            raise KeyError(f"Subsystem is not registered: {subsystem!r}")
        method = getattr(service, "invoke", None)
        if not callable(method):
            raise TypeError(f"Subsystem {subsystem!r} does not support invoke()")
        return method(payload)

    def handle_event(self, event: str, payload: Any = None) -> Any:
        if not event:
            raise ValueError("event is required")
        service = self._resolve(event)
        handler = getattr(service, "handle_event", None) if service is not None else None
        if callable(handler):
            return handler(payload)
        return self.invoke(event, payload)

    def _resolve(self, name: str) -> Any | None:
        return self._registry.get(name) if self._registry is not None else None

    @staticmethod
    def _call_lifecycle(service: Any, method_name: str, name: str) -> None:
        method = getattr(service, method_name, None)
        if callable(method):
            try:
                method()
            except Exception:
                logger.exception("Idle service %r failed during %s", name, method_name)