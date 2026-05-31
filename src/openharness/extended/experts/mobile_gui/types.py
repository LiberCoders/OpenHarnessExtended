"""Core data types for the mobile GUI agent."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from openharness.extended.experts.mobile_gui.device.results import DeviceActionResult


class ActionType(str, Enum):
    """Canonical GUI action vocabulary — the single source of truth.

    Backends map their model's native action names onto these; the executor
    dispatches them. Device primitives map 1:1 to ``MobileDeviceDriver``
    methods, so this enum and the driver interface evolve together.
    ``TERMINATE``/``INTERACT`` are control actions handled by the executor/loop
    and are never sent to the driver.
    """

    # Device primitives (1:1 with MobileDeviceDriver methods)
    CLICK = "click"
    LONG_PRESS = "long_press"
    SWIPE = "swipe"
    DRAG = "drag"
    TYPE = "type"
    OPEN = "open"
    KEY = "key"
    HOME = "home"
    BACK = "back"
    WAIT = "wait"
    # Control actions (handled by executor/loop, not the driver)
    TERMINATE = "terminate"
    INTERACT = "interact"


@dataclass
class Observation:
    """Per-step observation from the mobile device."""

    screenshot_path: str
    note: str = ""


@dataclass
class GuiAction:
    """A single GUI action produced by a backend.

    ``action`` is drawn from the canonical ``ActionType`` vocabulary; backends
    are responsible for mapping their model's native action names onto it.
    Coordinates are device pixels once ``adapt_action`` has run.
    """

    action: ActionType
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    seconds: float | None = None
    keycode: int | None = None
    status: str | None = None
    message: str = ""
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view used for step journaling / leader events."""
        return {
            # ActionType is a str-Enum; store its plain value. getattr keeps this
            # robust if a raw string ever slips in — journaling must not crash.
            "action": getattr(self.action, "value", self.action),
            "x": self.x,
            "y": self.y,
            "x2": self.x2,
            "y2": self.y2,
            "seconds": self.seconds,
            "status": self.status,
            "message": self.message,
            "text": self.text,
            "keycode": self.keycode,
        }


@dataclass
class GuiActionResult:
    """One ``GuiAction`` paired with the driver result it produced.

    The executor stitches the action layer (``GuiAction``) to the driver layer
    (``DeviceActionResult``) so a backend can read, per action, what the device
    actually did — including each underlying command's stdout/stderr/exit_code.
    """

    action: GuiAction
    device_result: DeviceActionResult

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view: the action plus its per-command outcomes."""
        dev = self.device_result
        return {
            "action": self.action.to_dict(),
            "ok": dev.ok,
            "output_path": dev.output_path,
            "commands": [
                {
                    "command": cmd.command,
                    "exit_code": cmd.exit_code,
                    "stdout": cmd.stdout,
                    "stderr": cmd.stderr,
                    "ok": cmd.ok,
                }
                for cmd in dev.results
            ],
        }


@dataclass
class ReasoningOutcome:
    """Reasoning output: raw response plus the parsed and adapted action plans.

    Both plans are lists to support composite actions (e.g. click + type) and
    backend-private remapping. ``adapted_actions`` is what the executor runs.
    """

    raw_response: str
    reasoning_content: str
    model_request: dict[str, Any] | None
    parsed_actions: list[GuiAction]
    adapted_actions: list[GuiAction]


@dataclass
class InferResult:
    """Inference output returned by GUI backend infer()."""

    raw_response: str
    model_request: dict[str, Any] | None
    reasoning_content: str = ""
