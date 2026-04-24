"""Action execution for mobile GUI agent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from openharness.extended.experts.mobile_gui.context import MobileGuiContext
from openharness.extended.experts.mobile_gui.device.base import MobileDeviceDriver
from openharness.extended.experts.mobile_gui.device.results import DeviceActionResult
from openharness.extended.experts.mobile_gui.types import GuiAction

ActionRunner = Callable[[GuiAction], Awaitable[tuple[str, DeviceActionResult]]]


class ActionExecutor:
    """Execute normalized GUI actions on the target device."""

    def __init__(self, *, driver: MobileDeviceDriver) -> None:
        self._driver = driver
        self._dispatch: dict[str, ActionRunner] = {
            "click": self._run_click,
            "wait": self._run_wait,
            "long_press": self._run_long_press,
            "swipe": self._run_swipe,
            "type": self._run_type,
            "open": self._run_open,
            "home": self._run_home,
            "back": self._run_back,
            "key": self._run_key,
            "interact": self._run_interact,
        }

    async def execute(self, action: GuiAction, context: MobileGuiContext) -> None:
        if action.action == "terminate":
            context.last_action = f"terminate({action.status or 'success'})"
            context.last_message = action.message
            context.done = True
            return

        runner = self._dispatch.get(action.action)
        if runner is None:
            context.last_action = f"{action.action}:unsupported"
            context.last_message = f"unsupported action: {action.action}"
            raise RuntimeError(context.last_message)

        action_desc, result = await runner(action)
        context.last_action = action_desc if result.ok else f"{action_desc}:failed"
        context.last_message = _format_result_message(
            action_desc=action_desc, result=result, action_message=action.message
        )
        if not result.ok:
            raise RuntimeError(context.last_message)

    async def _run_click(self, action: GuiAction) -> tuple[str, DeviceActionResult]:
        if action.x is None or action.y is None:
            raise RuntimeError("click action requires both x and y coordinates")
        return f"click({action.x},{action.y})", await self._driver.click(action.x, action.y)

    async def _run_wait(self, action: GuiAction) -> tuple[str, DeviceActionResult]:
        seconds = action.seconds if action.seconds is not None else 1.0
        return f"wait({seconds})", await self._driver.wait(float(seconds))

    async def _run_long_press(self, action: GuiAction) -> tuple[str, DeviceActionResult]:
        if action.x is None or action.y is None:
            raise RuntimeError("long_press requires coordinate")
        duration_ms = int(round(float(action.seconds) * 1000)) if action.seconds is not None else None
        return (
            f"long_press({action.x},{action.y})",
            await self._driver.long_press(action.x, action.y, duration_ms),
        )

    async def _run_swipe(self, action: GuiAction) -> tuple[str, DeviceActionResult]:
        if None in (action.x, action.y, action.x2, action.y2):
            raise RuntimeError("swipe requires coordinate and coordinate2")
        duration_ms = int(round(float(action.seconds) * 1000)) if action.seconds is not None else None
        return (
            f"swipe({action.x},{action.y},{action.x2},{action.y2})",
            await self._driver.swipe(action.x, action.y, action.x2, action.y2, duration_ms),
        )

    async def _run_type(self, action: GuiAction) -> tuple[str, DeviceActionResult]:
        text = action.text or ""
        return f"type(len={len(text)})", await self._driver.type_text(text)

    async def _run_open(self, action: GuiAction) -> tuple[str, DeviceActionResult]:
        query = (action.text or "").strip()
        if not query:
            raise RuntimeError("open action requires text (app name or package)")
        return f"open({query!r})", await self._driver.open_app(query)

    async def _run_home(self, action: GuiAction) -> tuple[str, DeviceActionResult]:
        return "home()", await self._driver.home()

    async def _run_back(self, action: GuiAction) -> tuple[str, DeviceActionResult]:
        return "back()", await self._driver.back()

    async def _run_key(self, action: GuiAction) -> tuple[str, DeviceActionResult]:
        if action.keycode is None:
            raise RuntimeError("key action requires keycode")
        return f"key({action.keycode})", await self._driver.keyevent(action.keycode)

    async def _run_interact(self, action: GuiAction) -> tuple[str, DeviceActionResult]:
        hint = (action.text or action.message or "").strip() or "operator assistance"
        raise RuntimeError(
            f"interact: automated worker cannot pause for manual UI; hint={hint!r}. "
            "Complete the step on the device and re-run or extend the worker channel."
        )


def _format_result_message(
    *, action_desc: str, result: DeviceActionResult, action_message: str
) -> str:
    parts = [f"{action_desc}: {'ok' if result.ok else 'failed'}"]
    for idx, cmd_result in enumerate(result.results, start=1):
        parts.extend(
            [
                f"cmd[{idx}]={cmd_result.command}",
                f"exit_code[{idx}]={cmd_result.exit_code}",
                f"stdout[{idx}]={cmd_result.stdout or '(empty)'}",
                f"stderr[{idx}]={cmd_result.stderr or '(empty)'}",
            ]
        )
    if action_message:
        parts.append(f"note={action_message}")
    return " | ".join(parts)
