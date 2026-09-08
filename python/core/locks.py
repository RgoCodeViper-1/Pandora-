"""Concurrency policy and keyed execution locks for Pandora.

Rules:
* Every intent gets a per-resource lock.
* A resource is exclusive, preventing overlapping mutations.
* Locks are re-entrant for the owning thread.
* Acquisition has a finite timeout and never waits forever.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Mapping


class LockTimeoutError(TimeoutError):
    """Raised when an execution lock cannot be acquired in time."""


@dataclass(frozen=True)
class LockRule:
    resource: str
    timeout_seconds: float = 10.0


class ExecutionLockManager:
    """Process-local keyed lock manager with deterministic resource rules."""

    def __init__(self, default_timeout: float = 10.0) -> None:
        if default_timeout <= 0:
            raise ValueError("default_timeout must be greater than zero")
        self.default_timeout = default_timeout
        self._registry_lock = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def _get_lock(self, resource: str) -> threading.RLock:
        with self._registry_lock:
            return self._locks.setdefault(resource, threading.RLock())

    def rule_for(self, intent_name: str, slots: Mapping[str, Any] | None = None) -> LockRule:
        slots = slots or {}
        explicit = slots.get("lock_key")
        if isinstance(explicit, str) and explicit.strip():
            resource = explicit.strip()
        elif intent_name in {"note_create", "task_add"}:
            resource = f"memory:{intent_name}"
        elif intent_name in {"app_open", "app_close"}:
            resource = f"application:{slots.get('app', slots.get('application', 'default'))}"
        elif intent_name in {"shutdown", "set_mode", "wake_up", "go_to_sleep"}:
            resource = "runtime-state"
        else:
            resource = f"intent:{intent_name}"
        return LockRule(resource, self.default_timeout)

    @contextmanager
    def for_intent(
        self,
        intent_name: str,
        slots: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Iterator[LockRule]:
        rule = self.rule_for(intent_name, slots)
        wait = rule.timeout_seconds if timeout is None else timeout
        if wait <= 0:
            raise ValueError("timeout must be greater than zero")
        lock = self._get_lock(rule.resource)
        if not lock.acquire(timeout=wait):
            raise LockTimeoutError(
                f"Timed out acquiring execution lock for {rule.resource!r}"
            )
        try:
            yield rule
        finally:
            lock.release()
