"""Tool for delegating experts through a stable protocol."""

from __future__ import annotations

import json
import multiprocessing as mp
import os
from dataclasses import asdict, is_dataclass
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from openharness.config import load_settings
from openharness.config.paths import get_config_dir, get_data_dir, get_logs_dir
from openharness.extended.channel import ExpertHandle, create_channel, get_channel_registry
from openharness.extended.engine.unified_loop import (
    FALLBACK_EXPERT_TYPE_SLUG,
    WORKER_IMPLEMENTED_EXPERT_TYPES,
    run_expert_worker_entry,
)
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

# Same set as the worker loop; extend in unified_loop when adding expert implementations.
SUPPORTED_EXPERT_TYPES: frozenset[str] = WORKER_IMPLEMENTED_EXPERT_TYPES

# Full parent `load_settings()` snapshot (JSON-friendly) merged into worker `runtime_overrides`.
OPENHARNESS_PARENT_SETTINGS_KEY = "openharness_parent_settings"


def _settings_snapshot_for_spawn(settings: Any) -> dict[str, Any]:
    """Serialize effective Settings for the child config payload (Pydantic or simple test doubles)."""
    model_dump = getattr(settings, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if is_dataclass(settings) and not isinstance(settings, type):
        return json.loads(json.dumps(asdict(settings), default=str))
    return {}


def _expert_id_prefix(expert_type: str) -> str:
    """Filesystem-safe prefix derived from expert_type."""
    cleaned = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in expert_type.strip().lower())
    return cleaned or FALLBACK_EXPERT_TYPE_SLUG


class _SubprocessProcessHandle:
    """Small adapter that mirrors the Process API used by channel tools."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self._proc = proc

    @property
    def pid(self) -> int:
        return int(self._proc.pid)

    @property
    def exitcode(self) -> int | None:
        return self._proc.poll()

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def terminate(self) -> None:
        self._proc.terminate()

    def kill(self) -> None:
        self._proc.kill()


class DelegateToExpertToolInput(BaseModel):
    """Arguments for delegate_to_expert."""

    expert_type: str = Field(
        description=(
            "Heterogeneous expert type to spawn (e.g. \"mobile_gui\"). "
            "Unsupported types are rejected; extend WORKER_IMPLEMENTED_EXPERT_TYPES in unified_loop when adding workers."
        )
    )
    task: str = Field(
        description=(
            "Task description for the delegated expert, "
            "e.g. operating an app on a connected mobile device. "
            "Please use Chinese to briefly describe the task."
        )
    )
    capability_profile: str = Field(default="hdc_minimal_v1")
    max_steps: int = Field(default=8, ge=1, le=100)
    device_serial: str | None = Field(default=None)
    runtime_overrides: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional runtime overrides for the worker; spawn merges parent profile fields "
            f"and injects full settings as {OPENHARNESS_PARENT_SETTINGS_KEY} unless that key is already set."
        ),
    )

class DelegateToExpertTool(BaseTool):
    """Spawn a typed expert subprocess with a dedicated duplex channel."""

    name = "delegate_to_expert"
    description = (
        "Delegate to an expert subprocess for non-CLI tasks; set expert_type "
        "(e.g. mobile_gui). Each type runs a dedicated worker entry in extended/engine."
    )
    input_model = DelegateToExpertToolInput

    @staticmethod
    def _parent_env_overrides() -> dict[str, str]:
        """Capture parent process OpenHarness directories for child reuse."""
        return {
            "OPENHARNESS_CONFIG_DIR": str(Path(os.environ.get("OPENHARNESS_CONFIG_DIR") or get_config_dir()).resolve()),
            "OPENHARNESS_DATA_DIR": str(Path(os.environ.get("OPENHARNESS_DATA_DIR") or get_data_dir()).resolve()),
            "OPENHARNESS_LOGS_DIR": str(Path(os.environ.get("OPENHARNESS_LOGS_DIR") or get_logs_dir()).resolve()),
        }

    def _build_runtime_overrides(self, requested: dict[str, Any] | None) -> dict[str, Any]:
        """Merge explicit runtime overrides with the parent process effective settings."""
        merged = dict(requested or {})
        try:
            settings = load_settings()
        except Exception:
            return merged

        if settings.active_profile:
            merged.setdefault("active_profile", settings.active_profile)
        if settings.model:
            merged.setdefault("model", settings.model)
        if settings.base_url:
            merged.setdefault("base_url", settings.base_url)
        if settings.api_format:
            merged.setdefault("api_format", settings.api_format)

        permission = getattr(settings, "permission", None)
        permission_mode = getattr(permission, "mode", None)
        if permission_mode is not None and "permission_mode" not in merged:
            merged["permission_mode"] = str(getattr(permission_mode, "value", permission_mode))

        snapshot = _settings_snapshot_for_spawn(settings)
        if snapshot:
            merged.setdefault(OPENHARNESS_PARENT_SETTINGS_KEY, snapshot)
        return merged

    @staticmethod
    def _write_initial_meta(state_root: Path, *, expert_id: str, arguments: DelegateToExpertToolInput, runtime_overrides: dict[str, Any]) -> None:
        state_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "expert_id": expert_id,
            "expert_type": arguments.expert_type,
            "task": arguments.task,
            "capability_profile": arguments.capability_profile,
            "runtime_overrides": runtime_overrides,
        }
        (state_root / "meta.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _spawn_worker(self, *, config: dict, channel) -> Any:
        if os.name == "nt":
            # In React-TUI backend mode on Windows, the backend host itself is a
            # child process with stdio pipes. Spawning a multiprocessing child
            # from there can fail with WinError 5 on DuplicateHandle. Launching
            # the worker via subprocess avoids that handle inheritance path.
            script = (
                "import json,sys;"
                "from openharness.extended.engine.unified_loop import run_expert_worker_entry;"
                "cfg=json.loads(sys.argv[1]);"
                "run_expert_worker_entry(cfg, sys.argv[2], sys.argv[3])"
            )
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    json.dumps(config, ensure_ascii=False),
                    str(channel.downlink_queue),
                    str(channel.uplink_queue),
                ],
                cwd=str(config.get("cwd") or Path.cwd()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return _SubprocessProcessHandle(proc)

        ctx = mp.get_context("spawn")
        process = ctx.Process(
            target=run_expert_worker_entry,
            args=(config, channel.downlink_queue, channel.uplink_queue),
            daemon=False,
        )
        process.start()
        return process

    async def execute(self, arguments: DelegateToExpertToolInput, context: ToolExecutionContext) -> ToolResult:
        expert_type = str(arguments.expert_type or "").strip()
        if expert_type not in SUPPORTED_EXPERT_TYPES:
            supported = ", ".join(sorted(SUPPORTED_EXPERT_TYPES))
            return ToolResult(
                output=f"unsupported expert_type={expert_type!r}; supported: {supported}",
                is_error=True,
            )

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        expert_id = f"{_expert_id_prefix(expert_type)}_{timestamp}_{uuid4().hex[:6]}"
        channel = create_channel(expert_id)
        runtime_overrides = self._build_runtime_overrides(arguments.runtime_overrides)
        env_overrides = self._parent_env_overrides()
        state_root = Path(env_overrides["OPENHARNESS_DATA_DIR"]).resolve() / "extended" / "experts" / expert_id
        self._write_initial_meta(
            state_root,
            expert_id=expert_id,
            arguments=arguments,
            runtime_overrides=runtime_overrides,
        )
        config = {
            "expert_type": expert_type,
            "expert_id": expert_id,
            "task": arguments.task,
            "capability_profile": arguments.capability_profile,
            "max_steps": arguments.max_steps,
            "device_serial": arguments.device_serial,
            "cwd": str(Path(context.cwd).resolve()),
            "runtime_overrides": runtime_overrides,
            "env_overrides": env_overrides,
        }
        process = self._spawn_worker(
            config=config,
            channel=channel,
        )
        state_root_str = str(state_root.resolve())

        handle = ExpertHandle(
            expert_id=expert_id,
            expert_type=arguments.expert_type,
            task_id=expert_id,
            channel=channel,
            process=process,
            task=arguments.task,
            metadata={
                "capability_profile": arguments.capability_profile,
                "state_root": state_root_str,
            },
        )
        registry = get_channel_registry()
        registry.register(handle)

        # Fire-and-forget: the worker process is spawned and we return immediately —
        # no waiting on startup. delegate already knows everything the leader needs
        # to interpret later step paths (task + both roots), so it states the path
        # convention right here instead of round-tripping through the worker uplink.
        # Process liveness and any bootstrap failure are observed later via
        # get_expert_status (process stopped + synthesized result.json).
        path_note = (
            "step file paths are cwd-relative (to working_dir) when under it, else absolute; "
            "pass them to Read as-is"
        )
        startup_block = (
            "\nStartup info (path convention for this expert's step artifacts):\n"
            f"  task: {arguments.task}\n"
            f"  working_dir: {Path(context.cwd).resolve()}\n"
            f"  state_root: {state_root_str}\n"
            f"  max_steps: {arguments.max_steps}\n"
            f"  note: {path_note}"
        )
        return ToolResult(
            output=(
                f"Delegated expert {expert_id} "
                f"(expert_id={expert_id}, task_id={expert_id}, pid={process.pid}). "
                f"Use get_expert_status/send_to_expert for experts."
                f"{startup_block}"
            ),
            metadata={
                "expert_id": expert_id,
                "task_id": expert_id,
                "expert_type": arguments.expert_type,
                "state_root": state_root_str,
            },
        )

