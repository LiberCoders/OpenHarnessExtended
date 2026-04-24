"""Runtime context for mobile GUI loop."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MobileGuiContext:
    """Mutable runtime state carried across loop iterations."""

    # Task input
    # Primary goal assigned when the worker starts.
    task: str
    # Runtime-appended guidance sent from leader via instruction_append.
    extra_instruction: str = ""

    # Loop progress
    step: int = 0
    done: bool = False

    # Last step outputs
    last_action: str = ""
    last_message: str = ""
    last_screenshot: str = ""

