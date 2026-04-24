"""Action execution for mobile GUI agent."""

from __future__ import annotations

from openharness.extended.experts.mobile_gui.context import MobileGuiContext
from openharness.extended.experts.mobile_gui.device.hdc import DeviceActionResult, HdcMobileDeviceDriver
from openharness.extended.experts.mobile_gui.types import GuiAction


class ActionExecutor:
    """Execute normalized GUI actions on the target device."""

    def __init__(self, *, driver: HdcMobileDeviceDriver) -> None:
        self._driver = driver

    async def execute(self, action: GuiAction, context: MobileGuiContext) -> None:
        if action.action == "click":
            if action.x is not None and action.y is not None:
                result = await self._driver.click(action.x, action.y)
                action_desc = f"click({action.x},{action.y})"
                context.last_action = action_desc if result.ok else f"{action_desc}:failed"
                context.last_message = _format_result_message(
                    action_desc=action_desc, result=result, action_message=action.message
                )
                if not result.ok:
                    raise RuntimeError(context.last_message)
            else:
                context.last_action = "click(invalid):failed"
                context.last_message = "click action requires both x and y coordinates"
                raise RuntimeError(context.last_message)
        elif action.action == "terminate":
            context.last_action = f"terminate({action.status or 'success'})"
            context.last_message = action.message
            context.done = True
        elif action.action == "wait":
            seconds = action.seconds or 1.0
            result = await self._driver.wait(seconds)
            action_desc = f"wait({seconds})"
            context.last_action = action_desc if result.ok else f"{action_desc}:failed"
            context.last_message = _format_result_message(
                action_desc=action_desc, result=result, action_message=action.message
            )
            if not result.ok:
                raise RuntimeError(context.last_message)
        else:
            context.last_action = f"{action.action}:unsupported"
            context.last_message = f"unsupported action: {action.action}"
            raise RuntimeError(context.last_message)


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
