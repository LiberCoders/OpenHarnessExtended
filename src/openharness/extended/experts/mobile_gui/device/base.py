"""Driver abstraction for mobile GUI device operations."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from openharness.extended.experts.mobile_gui.device.results import DeviceActionResult, DeviceCommandResult


def unsupported_action_result(action: str, transport: str) -> DeviceActionResult:
    msg = f"action {action!r} is not supported by transport={transport}"
    return DeviceActionResult(
        ok=False,
        results=[
            DeviceCommandResult(
                command=f"{transport}:{action}",
                exit_code=-1,
                stdout="",
                stderr=msg,
                ok=False,
            )
        ],
    )


class MobileDeviceDriver(ABC):
    """Base interface for all mobile device transports."""

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
        return unsupported_action_result("long_press", self.transport_name)

    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int | None = None
    ) -> DeviceActionResult:
        return unsupported_action_result("swipe", self.transport_name)

    async def type_text(self, text: str) -> DeviceActionResult:
        return unsupported_action_result("type", self.transport_name)

    async def open_app(self, app_or_package: str) -> DeviceActionResult:
        return unsupported_action_result("open", self.transport_name)

    async def keyevent(self, keycode: int) -> DeviceActionResult:
        return unsupported_action_result("key", self.transport_name)

    async def home(self) -> DeviceActionResult:
        return unsupported_action_result("home", self.transport_name)

    async def back(self) -> DeviceActionResult:
        return unsupported_action_result("back", self.transport_name)

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
