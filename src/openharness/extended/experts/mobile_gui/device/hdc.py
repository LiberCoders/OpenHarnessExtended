"""hdc-based mobile device driver for HarmonyOS."""

from __future__ import annotations

import asyncio
import shlex
import shutil
from pathlib import Path

from openharness.extended.experts.mobile_gui.device.base import MobileDeviceDriver
from openharness.extended.experts.mobile_gui.device.package_aliases import (
    get_launch_block,
    get_main_ability_hint,
    resolve_open_candidates,
)
from openharness.extended.experts.mobile_gui.device.results import DeviceActionResult, DeviceCommandResult

# `uinput -T -m` smooth time is the gesture duration in ms (default 1000, max 15000).
_MAX_SMOOTH_TIME_MS = 15000
_DEFAULT_SWIPE_MS = 800
_DEFAULT_LONG_PRESS_MS = 800
# `uinput -T -g` drag requires press_time >= 500 and (total - press) >= 500.
_DRAG_PRESS_MS = 500
_DRAG_MIN_TOTAL_MS = _DRAG_PRESS_MS + 500
_DEFAULT_DRAG_MS = 1000
# HarmonyOS navigation keycodes (@ohos.multimodalInput.keyCode), verified on-device.
_KEYCODE_HOME = 1
_KEYCODE_BACK = 2


def _aa_launch_ok(result: DeviceCommandResult) -> bool:
    """hdc shell exits 0 even when aa reports failure; check stdout for error markers."""
    return result.ok and not result.stdout.startswith("error:")


class HdcMobileDeviceDriver(MobileDeviceDriver):
    """Execute actions through hdc on HarmonyOS devices.

    All input primitives go through ``uinput`` (the low-level event injector):
    plain screen coordinates and gesture durations map to it directly, keeping
    every action on one injection path. ``home``/``back`` use the verified
    navigation keycodes (HOME=1, BACK=2). Only ``open_app`` steps outside
    ``uinput``, using ``aa start`` to launch an app by bundle name.
    """
    transport_name = "hdc"

    def __init__(self, *, cwd: Path, serial: str | None = None, exec_path: str | None = None) -> None:
        self._cwd = cwd
        self._serial = serial
        self._hdc_bin = (exec_path or "").strip() or shutil.which("hdc") or ""

    def is_available(self) -> bool:
        return bool(self._hdc_bin)

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

    async def long_press(self, x: int, y: int, duration_ms: int | None = None) -> DeviceActionResult:
        # Press down, hold for `duration_ms` (via -i interval), then release in place.
        self._require_hdc_available()
        hold = max(1, int(duration_ms) if duration_ms is not None else _DEFAULT_LONG_PRESS_MS)
        result = await self._run(
            ["shell", "uinput", "-T", "-d", str(x), str(y), "-i", str(hold), "-u", str(x), str(y)]
        )
        return DeviceActionResult(ok=result.ok, results=[result])

    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int | None = None
    ) -> DeviceActionResult:
        # `-m` smooth time IS the gesture duration in ms — no velocity conversion.
        self._require_hdc_available()
        smooth = int(duration_ms) if duration_ms is not None else _DEFAULT_SWIPE_MS
        smooth = max(1, min(_MAX_SMOOTH_TIME_MS, smooth))
        result = await self._run(
            ["shell", "uinput", "-T", "-m", str(x1), str(y1), str(x2), str(y2), str(smooth)]
        )
        return DeviceActionResult(ok=result.ok, results=[result])

    async def drag(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int | None = None
    ) -> DeviceActionResult:
        # `-g` is a native drag with a long-press grab phase. duration_ms maps to
        # total time; the tool requires press >= 500ms and total - press >= 500ms.
        self._require_hdc_available()
        total = int(duration_ms) if duration_ms is not None else _DEFAULT_DRAG_MS
        total = max(_DRAG_MIN_TOTAL_MS, total)
        result = await self._run(
            [
                "shell", "uinput", "-T", "-g",
                str(x1), str(y1), str(x2), str(y2), str(_DRAG_PRESS_MS), str(total),
            ]
        )
        return DeviceActionResult(ok=result.ok, results=[result])

    async def type_text(self, text: str) -> DeviceActionResult:
        # uitest uiInput inputText injects text via the accessibility framework
        # and supports Unicode/CJK — uinput -K -t only handles ASCII keycodes.
        self._require_hdc_available()
        result = await self._run(["shell", "uitest", "uiInput", "text", shlex.quote(text)])
        return DeviceActionResult(ok=result.ok, results=[result])

    async def keyevent(self, keycode: int) -> DeviceActionResult:
        self._require_hdc_available()
        code = str(int(keycode))
        result = await self._run(["shell", "uinput", "-K", "-d", code, "-u", code])
        return DeviceActionResult(ok=result.ok, results=[result])

    async def home(self) -> DeviceActionResult:
        return await self.keyevent(_KEYCODE_HOME)

    async def back(self) -> DeviceActionResult:
        return await self.keyevent(_KEYCODE_BACK)

    async def _query_main_ability(self, bundle: str) -> str | None:
        """Return the bundle's declared mainElementName via bm dump, or None."""
        try:
            r = await self._run(["shell", "bm", "dump", "-n", bundle])
            for line in r.stdout.splitlines():
                if '"mainElementName"' in line:
                    value = line.split(":", 1)[1].strip().strip('",')
                    if value:
                        return value
        except Exception:
            pass
        return None

    async def open_app(self, app_or_package: str) -> DeviceActionResult:
        self._require_hdc_available()
        query = (app_or_package or "").strip()
        if not query:
            return DeviceActionResult(
                ok=False,
                results=[DeviceCommandResult(command="hdc:open", exit_code=-1, stdout="", stderr="empty app query", ok=False)],
            )
        candidates = resolve_open_candidates(query, transport="hdc")
        if not candidates:
            msg = f"no bundle alias found for {query!r}; pass a bundle name directly (e.g. com.example.app)"
            return DeviceActionResult(
                ok=False,
                results=[DeviceCommandResult(command="hdc:open", exit_code=-1, stdout="", stderr=msg, ok=False)],
            )
        first_result = None
        for bundle in candidates:
            blocked = get_launch_block(bundle)
            if blocked:
                ability, err_stdout = blocked
                r = DeviceCommandResult(
                    command=f"aa start -b {bundle} -a {ability}",
                    exit_code=0,
                    stdout=err_stdout,
                    stderr="",
                    ok=False,
                )
            else:
                main_ability = get_main_ability_hint(bundle) or await self._query_main_ability(bundle)
                cmd = (
                    ["shell", "aa", "start", "-b", bundle, "-a", main_ability]
                    if main_ability
                    else ["shell", "aa", "start", "-b", bundle]
                )
                r = await self._run(cmd)
            if first_result is None:
                first_result = r
            if _aa_launch_ok(r):
                return DeviceActionResult(ok=True, results=[r])
        msg = f"aa start failed for all candidates {candidates} (query={query!r})"
        return DeviceActionResult(
            ok=False,
            results=[first_result, DeviceCommandResult(command="hdc:open", exit_code=-1, stdout="", stderr=msg, ok=False)],
        )

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

    def _build_argv(self, tail_args: list[str]) -> list[str]:
        self._require_hdc_available()
        argv = [str(self._hdc_bin)]
        if self._serial:
            argv.extend(["-t", self._serial])
        argv.extend(tail_args)
        return argv

    def _require_hdc_available(self) -> None:
        if not self._hdc_bin:
            raise RuntimeError("hdc binary not found; set mobile_gui.transport_exec_path or add hdc to PATH")

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
