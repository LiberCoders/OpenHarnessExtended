"""ADB-based Android device driver."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from openharness.extended.experts.mobile_gui.device.base import MobileDeviceDriver
from openharness.extended.experts.mobile_gui.device.package_aliases import resolve_open_candidates
from openharness.extended.experts.mobile_gui.device.results import DeviceActionResult, DeviceCommandResult

_MIN_GESTURE_DURATION_MS = 50
_DEFAULT_LONG_PRESS_MS = 800
_DEFAULT_SWIPE_MS = 800
# Drag defaults longer than swipe so the gesture reads as a deliberate drag
# rather than a flick (also gives draganddrop's grab phase room).
_DEFAULT_DRAG_MS = 1000
_IME_SWITCH_DELAY_SECONDS = 0.1


class AdbMobileDeviceDriver(MobileDeviceDriver):
    """Execute GUI actions through the Android Debug Bridge."""
    transport_name = "adb"

    def __init__(
        self,
        *,
        cwd: Path,
        serial: str | None = None,
        exec_path: str | None = None,
        swipe_default_ms: int = _DEFAULT_SWIPE_MS,
        long_press_default_ms: int = _DEFAULT_LONG_PRESS_MS,
    ) -> None:
        self._cwd = cwd
        self._serial = serial
        self._adb_bin = (exec_path or "").strip() or shutil.which("adb") or ""
        self._swipe_default_ms = max(_MIN_GESTURE_DURATION_MS, int(swipe_default_ms))
        self._long_press_default_ms = max(_MIN_GESTURE_DURATION_MS, int(long_press_default_ms))

    def is_available(self) -> bool:
        return bool(self._adb_bin)

    def _prefix_argv(self) -> list[str]:
        if not self._adb_bin:
            raise RuntimeError("adb binary not found; set mobile_gui.transport_exec_path or install platform-tools")
        argv = [str(self._adb_bin)]
        if self._serial:
            argv.extend(["-s", self._serial])
        return argv

    async def screenshot(self, output_path: Path) -> DeviceActionResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return await self._screenshot_bytes(output_path)

    async def _screenshot_bytes(self, output_path: Path) -> DeviceActionResult:
        argv = self._prefix_argv() + ["exec-out", "screencap", "-p"]
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self._cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_data, stderr_data = await process.communicate()
        stderr_text = (stderr_data or b"").decode("utf-8", errors="replace").strip()
        display_argv = [Path(argv[0]).name, *argv[1:]]
        cmd_result = DeviceCommandResult(
            command=" ".join(display_argv),
            exit_code=int(process.returncode or 0),
            stdout="(binary omitted)",
            stderr=stderr_text,
            ok=process.returncode == 0,
        )
        if not cmd_result.ok or not stdout_data:
            return DeviceActionResult(ok=False, results=[cmd_result], output_path=str(output_path))
        if not (stdout_data.startswith(b"\x89PNG") or stdout_data.startswith(b"\xff\xd8")):
            return DeviceActionResult(
                ok=False,
                results=[
                    DeviceCommandResult(
                        command=cmd_result.command,
                        exit_code=cmd_result.exit_code,
                        stdout="(invalid image magic)",
                        stderr=stderr_text or "screencap returned non-image data",
                        ok=False,
                    )
                ],
                output_path=str(output_path),
            )
        output_path.write_bytes(stdout_data)
        if output_path.stat().st_size <= 0:
            raise RuntimeError(f"adb screenshot wrote empty file: {output_path}")
        return DeviceActionResult(ok=True, results=[cmd_result], output_path=str(output_path))

    async def click(self, x: int, y: int) -> DeviceActionResult:
        result = await self._run_shell(f"input tap {int(x)} {int(y)}")
        return DeviceActionResult(ok=result.ok, results=[result])

    async def long_press(self, x: int, y: int, duration_ms: int | None = None) -> DeviceActionResult:
        raw = self._long_press_default_ms if duration_ms is None else int(duration_ms)
        d = max(_MIN_GESTURE_DURATION_MS, raw)
        result = await self._run_shell(f"input swipe {int(x)} {int(y)} {int(x)} {int(y)} {d}")
        return DeviceActionResult(ok=result.ok, results=[result])

    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int | None = None
    ) -> DeviceActionResult:
        d = int(duration_ms) if duration_ms is not None else self._swipe_default_ms
        d = max(_MIN_GESTURE_DURATION_MS, d)
        result = await self._run_shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {d}")
        return DeviceActionResult(ok=result.ok, results=[result])

    async def drag(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int | None = None
    ) -> DeviceActionResult:
        # `input draganddrop` emits the long-press-then-move sequence apps need to
        # recognize a drag. It's Android 8.0+ only; on older devices the command
        # errors and surfaces as a soft failure for the backend to handle.
        d = int(duration_ms) if duration_ms is not None else _DEFAULT_DRAG_MS
        d = max(_MIN_GESTURE_DURATION_MS, d)
        result = await self._run_shell(f"input draganddrop {int(x1)} {int(y1)} {int(x2)} {int(y2)} {d}")
        return DeviceActionResult(ok=result.ok, results=[result])

    async def type_text(self, text: str) -> DeviceActionResult:
        """Input text via ADB Keyboard broadcast (requires com.android.adbkeyboard on device).

        Targets whichever field currently has focus (no coordinates).
        """
        results: list[DeviceCommandResult] = []
        escaped = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("'", "\\'")
        )
        for shell_fragment in (
            "ime enable com.android.adbkeyboard/.AdbIME",
            "ime set com.android.adbkeyboard/.AdbIME",
        ):
            r = await self._run_shell(shell_fragment)
            results.append(r)
            if not r.ok:
                return DeviceActionResult(ok=False, results=results)
        await asyncio.sleep(_IME_SWITCH_DELAY_SECONDS)
        r = await self._run_shell(f'am broadcast -a ADB_INPUT_TEXT --es msg "{escaped}"')
        results.append(r)
        if not r.ok:
            return DeviceActionResult(ok=False, results=results)
        await asyncio.sleep(_IME_SWITCH_DELAY_SECONDS)
        r = await self._run_shell("ime disable com.android.adbkeyboard/.AdbIME")
        results.append(r)
        return DeviceActionResult(ok=r.ok, results=results)

    async def back(self) -> DeviceActionResult:
        result = await self._run_shell("input keyevent 4")
        return DeviceActionResult(ok=result.ok, results=[result])

    async def keyevent(self, keycode: int) -> DeviceActionResult:
        result = await self._run_shell(f"input keyevent {int(keycode)}")
        return DeviceActionResult(ok=result.ok, results=[result])

    async def home(self) -> DeviceActionResult:
        result = await self._run_shell(
            "am start -a android.intent.action.MAIN -c android.intent.category.HOME"
        )
        return DeviceActionResult(ok=result.ok, results=[result])

    async def open_app(self, app_or_package: str) -> DeviceActionResult:
        app_query = app_or_package.strip()
        if not app_query:
            return DeviceActionResult(
                ok=False,
                results=[
                    DeviceCommandResult(
                        command="adb:open",
                        exit_code=-1,
                        stdout="",
                        stderr="empty package or app query",
                        ok=False,
                    )
                ],
            )
        list_res = await self.list_user_packages()
        if not list_res.ok:
            return DeviceActionResult(ok=False, results=list_res.results)
        installed = _parse_pm_list_packages(list_res.results[-1].stdout)
        candidates = resolve_open_candidates(app_query)
        for pkg in candidates:
            if pkg in installed:
                launch = await self._launch_package(pkg)
                return DeviceActionResult(ok=launch.ok, results=[*list_res.results, *launch.results])
        if "." in app_query and "/" not in app_query and " " not in app_query:
            launch = await self._launch_package(app_query)
            return DeviceActionResult(ok=launch.ok, results=[*list_res.results, *launch.results])
        msg = f"no installed package matched query={app_query!r}; candidates={candidates[:5]}"
        return DeviceActionResult(
            ok=False,
            results=[
                *list_res.results,
                DeviceCommandResult(
                    command="adb:open",
                    exit_code=-1,
                    stdout="",
                    stderr=msg,
                    ok=False,
                ),
            ],
        )

    async def list_user_packages(self) -> DeviceActionResult:
        """`pm list packages -3` — third-party packages only."""
        result = await self._run_shell("pm list packages -3")
        return DeviceActionResult(ok=result.ok, results=[result])

    async def _launch_package(self, package_name: str) -> DeviceActionResult:
        pkg = package_name.strip()
        if not pkg:
            return DeviceActionResult(
                ok=False,
                results=[
                    DeviceCommandResult(
                        command="adb:launch",
                        exit_code=-1,
                        stdout="",
                        stderr="empty package",
                        ok=False,
                    )
                ],
            )
        result = await self._run_shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
        return DeviceActionResult(ok=result.ok, results=[result])

    async def wait(self, seconds: float) -> DeviceActionResult:
        actual = max(0.0, float(seconds))
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

    async def _run_shell(self, shell_body: str) -> DeviceCommandResult:
        argv = self._prefix_argv() + ["shell", shell_body]
        return await self._run_exec(argv, capture_stdout=True)

    async def _run_exec(
        self,
        argv: list[str],
        *,
        capture_stdout: bool = True,
    ) -> DeviceCommandResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self._cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_data, stderr_data = await process.communicate()
        stderr_text = (stderr_data or b"").decode("utf-8", errors="replace").strip()
        if capture_stdout:
            stdout_text = (stdout_data or b"").decode("utf-8", errors="replace").strip()
        else:
            stdout_text = ""
        display_argv = [Path(argv[0]).name, *argv[1:]]
        return DeviceCommandResult(
            command=" ".join(display_argv),
            exit_code=int(process.returncode or 0),
            stdout=stdout_text,
            stderr=stderr_text,
            ok=process.returncode == 0,
        )


def _parse_pm_list_packages(stdout: str) -> set[str]:
    out: set[str] = set()
    for line in (stdout or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("package:"):
            s = s[len("package:") :]
        if "=" in s:
            _, s = s.split("=", 1)
        s = s.strip()
        if s:
            out.add(s)
    return out
