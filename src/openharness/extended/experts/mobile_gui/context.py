"""Runtime context for mobile GUI loop."""

from __future__ import annotations

from dataclasses import dataclass, field

from openharness.extended.experts.mobile_gui.types import GuiActionResult


@dataclass
class MobileGuiContext:
    """Mutable runtime state carried across loop iterations."""

    # Task input
    # Primary goal assigned when the worker starts.
    task: str
    # Runtime-appended guidance sent from leader via instruction_append.
    # Each entry: {"instruction": str, "timestamp": str (ISO-8601 UTC)}.
    # Backends read only the "instruction" field; "timestamp" is for auditing.
    extra_instructions: list[dict] = field(default_factory=list)

    # Loop progress
    step: int = 0
    done: bool = False

    # Last step outputs
    last_action: str = ""
    last_message: str = ""
    last_screenshot: str = ""
    # Structured results of the most recent step's actions, set by the executor.
    # A backend may read these in its next infer() to react to soft failures
    # (per-command stdout/stderr/exit_code) — using them is the backend's choice.
    last_results: list[GuiActionResult] = field(default_factory=list)

