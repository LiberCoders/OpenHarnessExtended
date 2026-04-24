"""Shared result types for mobile device drivers."""

from __future__ import annotations

from dataclasses import dataclass


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
