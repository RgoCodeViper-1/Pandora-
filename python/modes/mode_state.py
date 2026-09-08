"""State definitions shared by the mode manager and execution layer."""

from enum import Enum


class Mode(str, Enum):
    COMMAND = "command"
    CONVERSATION = "conversation"
    OPTIMISED_IDLE = "optimised_idle"