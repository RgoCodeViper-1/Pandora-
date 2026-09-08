"""Central mode lifecycle and interaction transition policy."""

from __future__ import annotations

import logging
from typing import Any

from .mode_state import Mode
from .optimized_idle import OptimizedIdle

logger = logging.getLogger("pandora.modes")


class ModeManager:
    def __init__(self, service_registry: Any | None = None) -> None:
        self.idle = OptimizedIdle(service_registry)
        self._mode = Mode.OPTIMISED_IDLE
        self.idle.enter()
        print("\033[90m[PANDORA] -- Optimized Idle\033[0m", flush=True)

    @property
    def mode(self) -> Mode:
        return self._mode

    def transition(self, mode: Mode | str) -> Mode:
        target = mode if isinstance(mode, Mode) else Mode(str(mode).lower())
        if target == self._mode:
            return target
        if target == Mode.OPTIMISED_IDLE:
            self.idle.enter()
        else:
            self.idle.leave()
        logger.info("Mode transition: %s -> %s", self._mode.value, target.value)
        mode_labels = {
            Mode.OPTIMISED_IDLE: "Optimized Idle",
            Mode.COMMAND: "Command",
            Mode.CONVERSATION: "Conversation",
        }
        print(f"\033[90m[PANDORA] -- {mode_labels[target]}\033[0m", flush=True)
        self._mode = target
        return target

    def wake(self) -> Mode:
        return self.transition(Mode.COMMAND)

    def complete_interaction(self, *, conversational: bool) -> Mode:
        return self.transition(Mode.CONVERSATION if conversational else Mode.OPTIMISED_IDLE)