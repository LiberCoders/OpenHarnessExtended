"""Minimal hdc-based mobile device driver."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DeviceCommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    ok: bool


@dataclass
class DeviceActionResult:
    ok: bool
    results: list[DeviceCommandResult]
    output_path: str | None = None


class HdcMobileDeviceDriver:
    """Execute minimal actions through hdc."""

    def __init__(self, *, cwd: Path, serial: str | None = None) -> None:
        self._cwd = cwd
        self._serial = serial
        self._hdc_bin = shutil.which("hdc")

    def is_available(self) -> bool:
        return self._hdc_bin is not None

    async def screenshot(self, output_path: Path) -> DeviceActionResult:
        self._require_hdc_available()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        remote_path = "/data/local/tmp/openharness_screen.jpeg"
        snap_result = await self._run(["shell", "snapshot_display", "-f", remote_path])
        recv_result = await self._run(["file", "recv", remote_path, str(output_path)])
        results = [snap_result, recv_result]
        if not snap_result.ok or not recv_result.ok:
            return DeviceActionResult(ok=False, results=results, output_path=str(output_path))
        if not output_path.is_file():
            raise RuntimeError(
                "hdc screenshot recv completed but local file was not created: "
                f"{output_path}"
            )
        if output_path.stat().st_size <= 0:
            raise RuntimeError(
                "hdc screenshot recv created empty file: "
                f"{output_path}"
            )
        return DeviceActionResult(ok=True, results=results, output_path=str(output_path))

    async def click(self, x: int, y: int) -> DeviceActionResult:
        self._require_hdc_available()
        result = await self._run(["shell", "uinput", "-T", "-c", str(x), str(y)])
        return DeviceActionResult(ok=result.ok, results=[result])

    async def wait(self, seconds: float) -> DeviceActionResult:
        actual = max(0.0, seconds)
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

    def _build_argv(self, tail_args: list[str]) -> list[str]:
        self._require_hdc_available()
        argv = [str(self._hdc_bin)]
        if self._serial:
            argv.extend(["-t", self._serial])
        argv.extend(tail_args)
        return argv

    def _require_hdc_available(self) -> None:
        if self._hdc_bin is None:
            raise RuntimeError("hdc binary not found in PATH")

    async def _run(self, tail_args: list[str]) -> DeviceCommandResult:
        argv = self._build_argv(tail_args)
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self._cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_data, stderr_data = await process.communicate()
        stdout_text = (stdout_data or b"").decode("utf-8", errors="replace").strip()
        stderr_text = (stderr_data or b"").decode("utf-8", errors="replace").strip()
        display_argv = [Path(argv[0]).name, *argv[1:]]
        result = DeviceCommandResult(
            command=" ".join(display_argv),
            exit_code=int(process.returncode or 0),
            stdout=stdout_text,
            stderr=stderr_text,
            ok=process.returncode == 0,
        )
        return result

