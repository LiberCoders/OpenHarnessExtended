"""Perception step for mobile GUI agent."""

from __future__ import annotations

from pathlib import Path

from openharness.extended.agents.mobile_gui.context import MobileGuiContext
from openharness.extended.agents.mobile_gui.device.hdc import HdcMobileDeviceDriver
from openharness.extended.agents.mobile_gui.types import Observation


class MobilePerception:
    """Collect screenshots from the mobile device."""

    def __init__(self, *, driver: HdcMobileDeviceDriver, steps_dir: Path) -> None:
        self._driver = driver
        self._steps_dir = steps_dir

    async def observe(self, context: MobileGuiContext) -> Observation:
        screenshot_path = self._steps_dir / f"step_{context.step:04d}" / "screenshot.jpeg"
        path = await self._driver.screenshot(screenshot_path)
        return Observation(screenshot_path=path, note=f"step={context.step}")

