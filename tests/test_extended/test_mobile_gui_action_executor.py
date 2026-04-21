from __future__ import annotations

import pytest

from openharness.extended.agents.mobile_gui.action_executor import ActionExecutor
from openharness.extended.agents.mobile_gui.context import MobileGuiContext
from openharness.extended.agents.mobile_gui.device.hdc import DeviceActionResult, DeviceCommandResult
from openharness.extended.agents.mobile_gui.types import GuiAction


class _FakeDriver:
    def __init__(self, *, click_ok: bool) -> None:
        self._click_ok = click_ok

    async def click(self, x: int, y: int) -> DeviceActionResult:
        cmd = DeviceCommandResult(
            command=f"hdc shell uinput -T -c {x} {y}",
            exit_code=0 if self._click_ok else 1,
            stdout="ok-output" if self._click_ok else "",
            stderr="" if self._click_ok else "click failed",
            ok=self._click_ok,
        )
        return DeviceActionResult(ok=self._click_ok, results=[cmd])

    async def wait(self, seconds: float) -> DeviceActionResult:
        return DeviceActionResult(
            ok=True,
            results=[
                DeviceCommandResult(
                    command=f"sleep({seconds})",
                    exit_code=0,
                    stdout="",
                    stderr="",
                    ok=True,
                )
            ],
        )


@pytest.mark.asyncio
async def test_action_executor_records_driver_result_on_click_success() -> None:
    context = MobileGuiContext(task="test")
    executor = ActionExecutor(driver=_FakeDriver(click_ok=True))  # type: ignore[arg-type]

    await executor.execute(GuiAction(action="click", x=100, y=200, message="tap"), context)

    assert context.last_action == "click(100,200)"
    assert "click(100,200): ok" in context.last_message
    assert "cmd[1]=hdc shell uinput -T -c 100 200" in context.last_message
    assert "exit_code[1]=0" in context.last_message
    assert "stdout[1]=ok-output" in context.last_message
    assert "stderr[1]=(empty)" in context.last_message
    assert "note=tap" in context.last_message


@pytest.mark.asyncio
async def test_action_executor_records_driver_result_on_click_failure() -> None:
    context = MobileGuiContext(task="test")
    executor = ActionExecutor(driver=_FakeDriver(click_ok=False))  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="click\\(100,200\\): failed"):
        await executor.execute(GuiAction(action="click", x=100, y=200), context)

    assert context.last_action == "click(100,200):failed"
    assert "cmd[1]=hdc shell uinput -T -c 100 200" in context.last_message
    assert "exit_code[1]=1" in context.last_message
    assert "stdout[1]=(empty)" in context.last_message
    assert "stderr[1]=click failed" in context.last_message
