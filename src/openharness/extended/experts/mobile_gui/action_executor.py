"""Action execution for mobile GUI agent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from openharness.extended.experts.mobile_gui.context import MobileGuiContext
from openharness.extended.experts.mobile_gui.device.base import MobileDeviceDriver, UnsupportedActionError
from openharness.extended.experts.mobile_gui.device.results import DeviceActionResult
from openharness.extended.experts.mobile_gui.types import ActionType, GuiAction, GuiActionResult

ActionRunner = Callable[[GuiAction], Awaitable[tuple[str, DeviceActionResult]]]


class ActionExecutor:
    """Execute normalized GUI actions on the target device.

    The dispatch table maps each device-primitive ``ActionType`` to a driver
    call — essentially identity, since the canonical vocabulary mirrors the
    driver interface. All semantic mapping (name remapping, composites,
    coordinate normalization) happens upstream in the backend.
    """

    def __init__(self, *, driver: MobileDeviceDriver) -> None:
        self._driver = driver
        self._dispatch: dict[ActionType, ActionRunner] = {
            ActionType.CLICK: self._run_click,
            ActionType.WAIT: self._run_wait,
            ActionType.LONG_PRESS: self._run_long_press,
            ActionType.SWIPE: self._run_swipe,
            ActionType.DRAG: self._run_drag,
            ActionType.TYPE: self._run_type,
            ActionType.OPEN: self._run_open,
            ActionType.HOME: self._run_home,
            ActionType.BACK: self._run_back,
            ActionType.KEY: self._run_key,
        }

    async def run(
        self, actions: list[GuiAction], context: MobileGuiContext
    ) -> list[GuiActionResult]:
        """Execute actions in order and return per-action results.

        Failure handling:
        - **Hard failure → raises** (the loop terminates the expert): a driver
          primitive the transport doesn't implement (``UnsupportedActionError``),
          an ``interact`` request (the worker can't pause for manual UI), or a
          malformed action.
        - **Soft failure** — a device command ran but reported ``ok=False``:
          recorded into ``context.last_results``; the remaining actions in *this*
          plan are skipped and we return **without raising**, so the backend can
          observe the result and retry on the next step.
        """
        results: list[GuiActionResult] = []
        for action in actions:
            if action.action == ActionType.TERMINATE:
                # Don't fabricate a verdict: a terminate without a status is
                # "unspecified", matching the loop's expert_done_unspecified —
                # ending without a status is NOT the same as succeeding.
                context.last_action = f"terminate({action.status or 'unspecified'})"
                context.last_message = action.message
                context.done = True
                break
            if action.action == ActionType.INTERACT:
                hint = (action.text or action.message or "").strip() or "operator assistance"
                raise UnsupportedActionError(
                    "interact",
                    self._driver.transport_name,
                    detail=(
                        f"automated worker cannot pause for manual UI; hint={hint!r}. "
                        "Complete the step on the device and re-run or extend the worker channel."
                    ),
                )
            runner = self._dispatch.get(action.action)
            if runner is None:
                raise UnsupportedActionError(str(action.action), self._driver.transport_name)

            action_desc, result = await runner(action)
            results.append(GuiActionResult(action=action, device_result=result))
            context.last_action = action_desc if result.ok else f"{action_desc}:failed"
            context.last_message = _format_result_message(
                action_desc=action_desc, result=result, action_message=action.message
            )
            if not result.ok:
                break  # soft failure: stop this plan, but do NOT raise

        context.last_results = results
        return results

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

    async def _run_drag(self, action: GuiAction) -> tuple[str, DeviceActionResult]:
        if None in (action.x, action.y, action.x2, action.y2):
            raise RuntimeError("drag requires coordinate and coordinate2")
        duration_ms = int(round(float(action.seconds) * 1000)) if action.seconds is not None else None
        return (
            f"drag({action.x},{action.y},{action.x2},{action.y2})",
            await self._driver.drag(action.x, action.y, action.x2, action.y2, duration_ms),
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
