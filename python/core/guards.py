"""Execution safety checks for the Pandora dispatcher."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any, Mapping

from .config import CentralConfig, get_config


class GuardViolation(PermissionError):
    """Raised when an operation violates the configured execution policy."""


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str = ""


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ExecutionGuard:
    """Fail-closed validation for intent payloads and subprocess requests."""

    def __init__(self, config: CentralConfig | None = None) -> None:
        self.config = config or get_config()

    def check_intent(self, intent_data: Mapping[str, Any]) -> GuardDecision:
        if not isinstance(intent_data, Mapping):
            return GuardDecision(False, "Intent payload must be an object")
        intent = intent_data.get("intent")
        if not isinstance(intent, str) or not intent.strip():
            return GuardDecision(False, "Intent name is required")
        execution_type = intent_data.get("execution_type", "Sync")
        if not isinstance(execution_type, str):
            return GuardDecision(False, "Execution type must be a string")
        slots = intent_data.get("slots", intent_data.get("entities", {}))
        if slots is not None and not isinstance(slots, Mapping):
            return GuardDecision(False, "Intent slots must be an object")
        if execution_type.casefold() == "subprocess":
            command = slots.get("command") if isinstance(slots, Mapping) else None
            return self.check_command(command)
        return GuardDecision(True)

    def require_intent(self, intent_data: Mapping[str, Any]) -> None:
        decision = self.check_intent(intent_data)
        if not decision.allowed:
            raise GuardViolation(decision.reason)

    def check_command(self, command: Any) -> GuardDecision:
        if not isinstance(command, str) or not command.strip():
            return GuardDecision(False, "A non-empty command is required")
        if _CONTROL_CHARS.search(command):
            return GuardDecision(False, "Control characters are not allowed in commands")
        if not bool(self.config.get("security.shellExecution", False)):
            return GuardDecision(False, "Shell execution is disabled by configuration")
        try:
            parts = shlex.split(command, posix=False)
        except ValueError as exc:
            return GuardDecision(False, f"Invalid command quoting: {exc}")
        if not parts:
            return GuardDecision(False, "A command executable is required")
        whitelist = self.config.get("security.whitelistedCommands", [])
        if whitelist:
            if not isinstance(whitelist, list) or not all(isinstance(item, str) for item in whitelist):
                return GuardDecision(False, "security.whitelistedCommands must be a list of strings")
            executable = parts[0].strip('"').casefold()
            allowed = {item.strip('"').casefold() for item in whitelist}
            if executable not in allowed:
                return GuardDecision(False, f"Command is not whitelisted: {parts[0]}")
        return GuardDecision(True)
