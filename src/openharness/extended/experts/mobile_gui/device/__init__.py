"""Mobile device drivers and factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from openharness.extended.experts.mobile_gui.device.adb import AdbMobileDeviceDriver
from openharness.extended.experts.mobile_gui.device.base import MobileDeviceDriver
from openharness.extended.experts.mobile_gui.device.hdc import HdcMobileDeviceDriver

DriverFactory = Callable[[Path, str | None, dict[str, Any]], MobileDeviceDriver]


def _build_adb_driver(cwd: Path, serial: str | None, opts: dict[str, Any]) -> MobileDeviceDriver:
    exec_path = opts.get("transport_exec_path")
    return AdbMobileDeviceDriver(
        cwd=cwd,
        serial=serial,
        exec_path=str(exec_path).strip() if isinstance(exec_path, str) and exec_path.strip() else None,
    )


def _build_hdc_driver(cwd: Path, serial: str | None, opts: dict[str, Any]) -> MobileDeviceDriver:
    exec_path = opts.get("transport_exec_path")
    return HdcMobileDeviceDriver(
        cwd=cwd,
        serial=serial,
        exec_path=str(exec_path).strip() if isinstance(exec_path, str) and exec_path.strip() else None,
    )


DRIVER_FACTORIES: dict[str, DriverFactory] = {
    "adb": _build_adb_driver,
    "hdc": _build_hdc_driver,
}


def create_mobile_driver(
    *,
    transport: str,
    cwd: Path,
    serial: str | None,
    options: dict[str, Any],
) -> MobileDeviceDriver:
    key = str(transport or "").strip().lower()
    if not key:
        raise RuntimeError("mobile_gui.device_transport is required and must be one of: adb, hdc")
    factory = DRIVER_FACTORIES.get(key)
    if factory is None:
        supported = ", ".join(sorted(DRIVER_FACTORIES))
        raise RuntimeError(f"unsupported mobile_gui.device_transport={transport!r}; supported: {supported}")
    driver = factory(cwd, serial, options)
    if not driver.is_available():
        raise RuntimeError(
            f"device transport {key!r} is configured but binary was not found. "
            "Install related CLI or configure explicit binary path."
        )
    return driver

