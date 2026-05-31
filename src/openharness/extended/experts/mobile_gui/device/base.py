"""Driver abstraction for mobile GUI device operations."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from openharness.extended.experts.mobile_gui.device.results import DeviceActionResult, DeviceCommandResult


class UnsupportedActionError(RuntimeError):
    """Raised when a transport does not implement a requested driver primitive.

    This is a *hard* failure: the loop terminates the expert. It is deliberately
    distinct from a driver method that runs a command the device rejects
    (``DeviceActionResult.ok is False``) — that is a *soft* failure the backend
    may observe and recover from.
    """

    def __init__(self, action: str, transport: str, detail: str = "") -> None:
        msg = f"action {action!r} is not supported by transport={transport}"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg)
        self.action = action
        self.transport = transport


class MobileDeviceDriver(ABC):
    """Base interface for all mobile device transports.

    The methods here are the canonical, transport-agnostic primitive vocabulary
    (kept in lockstep with ``ActionType``). ``adb`` implements all of them;
    thinner transports (e.g. ``hdc``) implement a subset and inherit the
    defaults below, which raise ``UnsupportedActionError`` — a hard failure.
    """

    transport_name: str = "unknown"

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def screenshot(self, output_path: Path) -> DeviceActionResult:
        raise NotImplementedError

    @abstractmethod
    async def click(self, x: int, y: int) -> DeviceActionResult:
        raise NotImplementedError

    async def long_press(self, x: int, y: int, duration_ms: int | None = None) -> DeviceActionResult:
        raise UnsupportedActionError("long_press", self.transport_name)

    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int | None = None
    ) -> DeviceActionResult:
        raise UnsupportedActionError("swipe", self.transport_name)

    async def drag(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int | None = None
    ) -> DeviceActionResult:
        # Drag = long-press-to-grab then move (rearranging icons, selecting text),
        # distinct from swipe's quick flick/scroll. Same coordinate signature.
        raise UnsupportedActionError("drag", self.transport_name)

    async def type_text(self, text: str) -> DeviceActionResult:
        # Text injection targets the currently focused field (no coordinates), so
        # the backend must focus it first — emit click(x, y) before type, or map a
        # "type at (x, y)" model action to a [click, type] composite.
        raise UnsupportedActionError("type", self.transport_name)

    async def open_app(self, app_or_package: str) -> DeviceActionResult:
        raise UnsupportedActionError("open", self.transport_name)

    async def keyevent(self, keycode: int) -> DeviceActionResult:
        raise UnsupportedActionError("key", self.transport_name)

    async def home(self) -> DeviceActionResult:
        raise UnsupportedActionError("home", self.transport_name)

    async def back(self) -> DeviceActionResult:
        raise UnsupportedActionError("back", self.transport_name)

    async def wait(self, seconds: float) -> DeviceActionResult:
        actual = max(0.0, float(seconds))
        await asyncio.sleep(actual)
        return DeviceActionResult(
            ok=True,
            results=[
                DeviceCommandResult(
                    command=f"sleep({actual})",
                    exit_code=0,
                    stdout="",
                    stderr="",
                    ok=True,
                )
            ],
        )
