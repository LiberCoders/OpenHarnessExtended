"""Minimal hdc-based mobile device driver."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

class HdcMobileDeviceDriver:
    """Execute minimal actions through hdc."""

    def __init__(self, *, cwd: Path, serial: str | None = None) -> None:
        self._cwd = cwd
        self._serial = serial

    def is_available(self) -> bool:
        return shutil.which("hdc") is not None

    async def screenshot(self, output_path: Path) -> str:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.is_available():
            output_path.write_text("hdc unavailable", encoding="utf-8")
            return str(output_path)

        remote_path = "/data/local/tmp/openharness_screen.jpeg"
        await self._run(["shell", "snapshot_display", "-f", remote_path])
        await self._run(["file", "recv", remote_path, str(output_path)])
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
        return str(output_path)

    async def click(self, x: int, y: int) -> None:
        if not self.is_available():
            return
        await self._run(["shell", "uinput", "-T", "-c", str(x), str(y)])

    async def wait(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))

    def _build_argv(self, tail_args: list[str]) -> list[str]:
        hdc_bin = shutil.which("hdc") or "hdc"
        argv = [hdc_bin]
        if self._serial:
            argv.extend(["-t", self._serial])
        argv.extend(tail_args)
        return argv

    async def _run(self, tail_args: list[str]) -> None:
        argv = self._build_argv(tail_args)
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self._cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_data, stderr_data = await process.communicate()
        if process.returncode == 0:
            return
        stderr_text = (stderr_data or b"").decode("utf-8", errors="replace").strip()
        stdout_text = (stdout_data or b"").decode("utf-8", errors="replace").strip()
        detail = stderr_text or stdout_text or "(no output)"
        raise RuntimeError(
            f"hdc command failed (exit_code={process.returncode}): {' '.join(argv)}\n{detail}"
        )

