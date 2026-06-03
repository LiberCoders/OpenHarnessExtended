from __future__ import annotations

from pathlib import Path

import pytest

from openharness.extended.experts.mobile_gui.action_executor import ActionExecutor
from openharness.extended.experts.mobile_gui.context import MobileGuiContext
from openharness.extended.experts.mobile_gui.device.adb import AdbMobileDeviceDriver
from openharness.extended.experts.mobile_gui.device.base import MobileDeviceDriver, UnsupportedActionError
from openharness.extended.experts.mobile_gui.device.results import DeviceActionResult, DeviceCommandResult
from openharness.extended.experts.mobile_gui.types import ActionType, GuiAction


class _FakeDriver:
    """Minimal driver exposing only click + wait (enough for soft-failure tests)."""

    transport_name = "fake"

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


class _ClickOnlyDriver(MobileDeviceDriver):
    """Real driver subclass: implements click, inherits the raising defaults."""

    transport_name = "clickonly"

    def is_available(self) -> bool:
        return True

    async def screenshot(self, output_path: Path) -> DeviceActionResult:
        return DeviceActionResult(ok=True, results=[], output_path=str(output_path))

    async def click(self, x: int, y: int) -> DeviceActionResult:
        cmd = DeviceCommandResult(
            command=f"tap {x} {y}", exit_code=0, stdout="", stderr="", ok=True
        )
        return DeviceActionResult(ok=True, results=[cmd])


@pytest.mark.asyncio
async def test_run_records_driver_result_on_click_success() -> None:
    context = MobileGuiContext(task="test")
    executor = ActionExecutor(driver=_FakeDriver(click_ok=True))  # type: ignore[arg-type]

    results = await executor.run(
        [GuiAction(action=ActionType.CLICK, x=100, y=200, message="tap")], context
    )

    assert len(results) == 1 and results[0].device_result.ok is True
    assert context.last_action == "click(100,200)"
    assert "click(100,200): ok" in context.last_message
    assert "cmd[1]=hdc shell uinput -T -c 100 200" in context.last_message
    assert "exit_code[1]=0" in context.last_message
    assert "stdout[1]=ok-output" in context.last_message
    assert "stderr[1]=(empty)" in context.last_message
    assert "note=tap" in context.last_message
    # Structured results are exposed on the context for the backend to read.
    assert context.last_results == results


@pytest.mark.asyncio
async def test_run_soft_failure_is_recorded_without_raising() -> None:
    """A device-reported ok=False is a *soft* failure: recorded, not raised."""
    context = MobileGuiContext(task="test")
    executor = ActionExecutor(driver=_FakeDriver(click_ok=False))  # type: ignore[arg-type]

    results = await executor.run([GuiAction(action=ActionType.CLICK, x=100, y=200)], context)

    assert len(results) == 1
    assert results[0].device_result.ok is False
    assert context.last_action == "click(100,200):failed"
    assert "exit_code[1]=1" in context.last_message
    assert "stderr[1]=click failed" in context.last_message
    assert context.last_results == results


@pytest.mark.asyncio
async def test_run_stops_remaining_actions_after_soft_failure() -> None:
    """In a composite plan, a soft failure skips the rest of the plan."""
    context = MobileGuiContext(task="test")
    executor = ActionExecutor(driver=_FakeDriver(click_ok=False))  # type: ignore[arg-type]

    plan = [
        GuiAction(action=ActionType.CLICK, x=1, y=2),
        GuiAction(action=ActionType.TYPE, text="hello"),  # must not run
    ]
    results = await executor.run(plan, context)

    assert len(results) == 1
    assert results[0].action.action == ActionType.CLICK


@pytest.mark.asyncio
async def test_run_unsupported_primitive_is_hard_failure() -> None:
    """A primitive the transport doesn't implement raises (hard failure)."""
    context = MobileGuiContext(task="test")
    executor = ActionExecutor(driver=_ClickOnlyDriver())

    with pytest.raises(UnsupportedActionError):
        await executor.run(
            [GuiAction(action=ActionType.SWIPE, x=1, y=1, x2=2, y2=2)], context
        )


@pytest.mark.asyncio
async def test_run_interact_is_hard_failure() -> None:
    context = MobileGuiContext(task="test")
    executor = ActionExecutor(driver=_ClickOnlyDriver())

    with pytest.raises(UnsupportedActionError):
        await executor.run([GuiAction(action=ActionType.INTERACT, text="help me")], context)


@pytest.mark.asyncio
async def test_run_terminate_sets_done_and_returns_no_results() -> None:
    context = MobileGuiContext(task="test")
    executor = ActionExecutor(driver=_ClickOnlyDriver())

    results = await executor.run(
        [GuiAction(action=ActionType.TERMINATE, status="success", message="done")], context
    )

    assert context.done is True
    assert results == []
    assert context.last_action == "terminate(success)"
    assert context.last_message == "done"


@pytest.mark.asyncio
async def test_run_terminate_without_status_is_unspecified_not_success() -> None:
    """A terminate with no verdict must not be labeled 'success'."""
    context = MobileGuiContext(task="test")
    executor = ActionExecutor(driver=_ClickOnlyDriver())

    await executor.run([GuiAction(action=ActionType.TERMINATE)], context)

    assert context.done is True
    assert context.last_action == "terminate(unspecified)"


def test_dispatch_covers_all_action_types() -> None:
    """Every ActionType is handled: device primitives via dispatch, control inline."""
    executor = ActionExecutor(driver=_ClickOnlyDriver())
    handled = set(executor._dispatch.keys()) | {ActionType.TERMINATE, ActionType.INTERACT}
    assert handled == set(ActionType)


@pytest.mark.asyncio
async def test_adb_drag_uses_draganddrop() -> None:
    driver = AdbMobileDeviceDriver(cwd=Path("."), exec_path="adb")

    async def fake_run_shell(body: str) -> DeviceCommandResult:
        captured["body"] = body
        return DeviceCommandResult(
            command=f"adb shell {body}", exit_code=0, stdout="", stderr="", ok=True
        )

    driver._run_shell = fake_run_shell  # type: ignore[method-assign]

    result = await driver.drag(10, 20, 30, 40, duration_ms=500)
    assert result.ok is True
    assert captured["body"] == "input draganddrop 10 20 30 40 500"

    # No duration -> the drag-specific default (1000ms), distinct from swipe.
    await driver.drag(10, 20, 30, 40)
    assert captured["body"] == "input draganddrop 10 20 30 40 1000"


@pytest.mark.asyncio
async def test_adb_list_user_packages_returns_action_result() -> None:
    driver = AdbMobileDeviceDriver(cwd=Path("."), exec_path="adb")

    async def fake_run_shell(_: str) -> DeviceCommandResult:
        return DeviceCommandResult(
            command="adb shell pm list packages -3",
            exit_code=0,
            stdout="package:com.example.app",
            stderr="",
            ok=True,
        )

    driver._run_shell = fake_run_shell  # type: ignore[method-assign]

    result = await driver.list_user_packages()

    assert isinstance(result, DeviceActionResult)
    assert result.ok is True
    assert len(result.results) == 1
    assert result.results[0].command == "adb shell pm list packages -3"
