"""Perception step for mobile GUI agent."""

from __future__ import annotations

from pathlib import Path

from openharness.extended.experts.mobile_gui.context import MobileGuiContext
from openharness.extended.experts.mobile_gui.device.base import MobileDeviceDriver
from openharness.extended.experts.mobile_gui.types import Observation


class MobilePerception:
    """Collect screenshots from the mobile device."""

    def __init__(self, *, driver: MobileDeviceDriver, steps_dir: Path) -> None:
        self._driver = driver
        self._steps_dir = steps_dir

    async def observe(self, context: MobileGuiContext) -> Observation:
        screenshot_path = self._steps_dir / f"step_{context.step:04d}" / "screenshot.jpeg"
        result = await self._driver.screenshot(screenshot_path)
        path = result.output_path or str(screenshot_path)
        if not result.ok:
            detail = " | ".join(
                [
                    f"cmd[{idx}]={item.command},exit={item.exit_code},stdout={item.stdout or '(empty)'},stderr={item.stderr or '(empty)'}"
                    for idx, item in enumerate(result.results, start=1)
                ]
            )
            raise RuntimeError(f"screenshot failed: {detail}")
        return Observation(screenshot_path=path, note=f"step={context.step}")

