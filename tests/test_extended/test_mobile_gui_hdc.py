from __future__ import annotations

from pathlib import Path

import pytest

from openharness.extended.experts.mobile_gui.device.hdc import HdcMobileDeviceDriver
from openharness.extended.experts.mobile_gui.device.results import DeviceCommandResult


def _driver_with_recorded_runs() -> tuple[HdcMobileDeviceDriver, list[list[str]]]:
    """An hdc driver whose `_run` records argv instead of spawning a process."""
    driver = HdcMobileDeviceDriver(cwd=Path("."))
    driver._hdc_bin = "hdc"  # pretend hdc is on PATH so _require_hdc_available passes
    calls: list[list[str]] = []

    async def fake_run(tail_args: list[str]) -> DeviceCommandResult:
        calls.append(tail_args)
        return DeviceCommandResult(
            command="hdc " + " ".join(tail_args), exit_code=0, stdout="", stderr="", ok=True
        )

    driver._run = fake_run  # type: ignore[method-assign]
    return driver, calls


@pytest.mark.asyncio
async def test_long_press_holds_via_uinput_down_interval_up() -> None:
    driver, calls = _driver_with_recorded_runs()
    result = await driver.long_press(10, 20, duration_ms=600)
    assert result.ok is True
    assert calls[0] == ["shell", "uinput", "-T", "-d", "10", "20", "-i", "600", "-u", "10", "20"]

    calls.clear()
    await driver.long_press(10, 20)  # default hold
    assert calls[0] == ["shell", "uinput", "-T", "-d", "10", "20", "-i", "800", "-u", "10", "20"]


@pytest.mark.asyncio
async def test_swipe_uses_uinput_move_with_duration_as_smooth_time() -> None:
    driver, calls = _driver_with_recorded_runs()
    # smooth time IS the duration in ms — passed through directly, no conversion.
    await driver.swipe(0, 0, 100, 0, duration_ms=1000)
    assert calls[0] == ["shell", "uinput", "-T", "-m", "0", "0", "100", "0", "1000"]

    calls.clear()
    await driver.swipe(0, 0, 100, 0)  # default duration
    assert calls[0] == ["shell", "uinput", "-T", "-m", "0", "0", "100", "0", "800"]

    calls.clear()
    await driver.swipe(0, 0, 100, 0, duration_ms=99999)  # clamped to the 15000 ceiling
    assert calls[0][-1] == "15000"


@pytest.mark.asyncio
async def test_drag_uses_uinput_native_drag_with_press_and_total() -> None:
    driver, calls = _driver_with_recorded_runs()
    result = await driver.drag(0, 0, 100, 0, duration_ms=2000)
    assert result.ok is True
    # press 500ms, total 2000ms.
    assert calls[0] == ["shell", "uinput", "-T", "-g", "0", "0", "100", "0", "500", "2000"]

    calls.clear()
    # Short duration floored so total - press >= 500 (min total = 1000).
    await driver.drag(0, 0, 100, 0, duration_ms=200)
    assert calls[0] == ["shell", "uinput", "-T", "-g", "0", "0", "100", "0", "500", "1000"]


@pytest.mark.asyncio
async def test_type_text_uses_uitest_uiinput_text() -> None:
    driver, calls = _driver_with_recorded_runs()
    result = await driver.type_text("hello world")
    assert result.ok is True
    assert calls[0] == ["shell", "uitest", "uiInput", "text", "'hello world'"]


@pytest.mark.asyncio
async def test_home_and_back_use_uinput_navigation_keycodes() -> None:
    driver, calls = _driver_with_recorded_runs()
    await driver.home()  # HOME = 1
    await driver.back()  # BACK = 2
    assert calls == [
        ["shell", "uinput", "-K", "-d", "1", "-u", "1"],
        ["shell", "uinput", "-K", "-d", "2", "-u", "2"],
    ]


@pytest.mark.asyncio
async def test_keyevent_uses_uinput_key_down_up() -> None:
    driver, calls = _driver_with_recorded_runs()
    await driver.keyevent(2049)
    assert calls[0] == ["shell", "uinput", "-K", "-d", "2049", "-u", "2049"]


@pytest.mark.asyncio
async def test_open_app_launches_bundle_via_aa_start() -> None:
    driver, calls = _driver_with_recorded_runs()
    # notepad is in the hint table → no bm dump, direct explicit launch
    result = await driver.open_app("com.huawei.hmos.notepad")
    assert result.ok is True
    assert calls == [["shell", "aa", "start", "-b", "com.huawei.hmos.notepad", "-a", "MainAbility"]]


@pytest.mark.asyncio
async def test_open_app_uses_bm_dump_for_unknown_bundle() -> None:
    """Bundles not in the hint table trigger a bm dump query before launching."""
    driver = HdcMobileDeviceDriver(cwd=Path("."))
    driver._hdc_bin = "hdc"
    calls: list[list[str]] = []

    async def fake_run(tail_args: list[str]) -> DeviceCommandResult:
        calls.append(tail_args)
        if tail_args[:3] == ["shell", "bm", "dump"]:
            return DeviceCommandResult(
                command="hdc " + " ".join(tail_args),
                exit_code=0,
                stdout='"mainElementName": "MainAbility",',
                stderr="",
                ok=True,
            )
        return DeviceCommandResult(
            command="hdc " + " ".join(tail_args), exit_code=0, stdout="", stderr="", ok=True
        )

    driver._run = fake_run  # type: ignore[method-assign]
    result = await driver.open_app("com.example.app")
    assert result.ok is True
    assert calls[0] == ["shell", "bm", "dump", "-n", "com.example.app"]
    assert calls[1] == ["shell", "aa", "start", "-b", "com.example.app", "-a", "MainAbility"]


@pytest.mark.asyncio
async def test_open_app_rejects_non_bundle_query_as_soft_failure() -> None:
    driver, calls = _driver_with_recorded_runs()
    result = await driver.open_app("NonExistentFakeApp")  # not in any alias table, not a bundle
    assert result.ok is False
    assert calls == []
    assert "bundle name" in result.results[0].stderr
