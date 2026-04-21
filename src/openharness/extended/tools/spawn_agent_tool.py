"""Tool for spawning heterogeneous agents through a stable protocol."""

from __future__ import annotations

import asyncio
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
from openharness.extended.channel import AgentHandle, create_channel, get_channel_registry
from openharness.extended.engine.unified_loop import (
    FALLBACK_AGENT_TYPE_SLUG,
    WORKER_IMPLEMENTED_AGENT_TYPES,
    run_heterogeneous_worker_entry,
)
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

# Same set as the worker loop; extend in unified_loop when adding agent implementations.
SUPPORTED_SPAWN_AGENT_TYPES: frozenset[str] = WORKER_IMPLEMENTED_AGENT_TYPES

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


def _agent_id_prefix(agent_type: str) -> str:
    """Filesystem-safe prefix derived from agent_type."""
    cleaned = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in agent_type.strip().lower())
    return cleaned or FALLBACK_AGENT_TYPE_SLUG


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


class SpawnAgentToolInput(BaseModel):
    """Arguments for spawn_agent."""

    agent_type: str = Field(
        description=(
            "Heterogeneous agent type to spawn (e.g. \"mobile_gui\"). "
            "Unsupported types are rejected; extend WORKER_IMPLEMENTED_AGENT_TYPES in unified_loop when adding workers."
        )
    )
    task: str = Field(
        description=(
            "Task description for the spawned heterogeneous agent, "
            "e.g. operating an app on a connected mobile device."
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

class SpawnAgentTool(BaseTool):
    """Spawn a typed agent subprocess with a dedicated duplex channel."""

    name = "spawn_agent"
    description = (
        "Spawn a heterogeneous agent subprocess for non-CLI tasks; set agent_type "
        "(e.g. mobile_gui). Each type runs a dedicated worker entry in extended/engine."
    )
    input_model = SpawnAgentToolInput

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
    def _write_initial_meta(state_root: Path, *, agent_id: str, arguments: SpawnAgentToolInput, runtime_overrides: dict[str, Any]) -> None:
        state_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "agent_id": agent_id,
            "agent_type": arguments.agent_type,
            "task": arguments.task,
            "capability_profile": arguments.capability_profile,
            "runtime_overrides": runtime_overrides,
        }
        (state_root / "meta.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _write_start_failure_result(state_root: Path, *, agent_id: str, exit_code: int | None) -> None:
        payload = {
            "agent_id": agent_id,
            "status": "failed",
            "last_action": "terminate(start_failed)",
            "message": f"failed_to_start(exit_code={exit_code})",
            "last_screenshot": "",
            "steps": 0,
        }
        (state_root / "result.json").write_text(
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
                "from openharness.extended.engine.unified_loop import run_heterogeneous_worker_entry;"
                "cfg=json.loads(sys.argv[1]);"
                "run_heterogeneous_worker_entry(cfg, sys.argv[2], sys.argv[3])"
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
            target=run_heterogeneous_worker_entry,
            args=(config, channel.downlink_queue, channel.uplink_queue),
            daemon=False,
        )
        process.start()
        return process

    async def execute(self, arguments: SpawnAgentToolInput, context: ToolExecutionContext) -> ToolResult:
        agent_type = str(arguments.agent_type or "").strip()
        if agent_type not in SUPPORTED_SPAWN_AGENT_TYPES:
            supported = ", ".join(sorted(SUPPORTED_SPAWN_AGENT_TYPES))
            return ToolResult(
                output=f"unsupported agent_type={agent_type!r}; supported: {supported}",
                is_error=True,
            )

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        agent_id = f"{_agent_id_prefix(agent_type)}_{timestamp}_{uuid4().hex[:6]}"
        channel = create_channel(agent_id)
        runtime_overrides = self._build_runtime_overrides(arguments.runtime_overrides)
        env_overrides = self._parent_env_overrides()
        state_root = Path(env_overrides["OPENHARNESS_DATA_DIR"]).resolve() / "extended" / "agents" / agent_id
        self._write_initial_meta(
            state_root,
            agent_id=agent_id,
            arguments=arguments,
            runtime_overrides=runtime_overrides,
        )
        config = {
            "agent_type": agent_type,
            "agent_id": agent_id,
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

        handle = AgentHandle(
            agent_id=agent_id,
            agent_type=arguments.agent_type,
            task_id=agent_id,
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
        started = False
        startup_deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < startup_deadline:
            registry.refresh(handle)
            if handle.last_action.startswith("startup:"):
                started = True
                break
            if not process.is_alive():
                break
            await asyncio.sleep(0.05)
        if not process.is_alive():
            exit_code = process.exitcode
            self._write_start_failure_result(
                state_root,
                agent_id=agent_id,
                exit_code=exit_code,
            )
            handle.status = "failed"
            channel.close()
            return ToolResult(
                output=(
                    f"failed to start agent {agent_id}: process exited early "
                    f"(exit_code={exit_code})."
                ),
                is_error=True,
                metadata={
                    "agent_id": agent_id,
                    "task_id": agent_id,
                    "agent_type": arguments.agent_type,
                    "state_root": state_root_str,
                },
            )
        if not started:
            # Slow startup is common on Windows; keep the worker alive and let
            # query_agent_status observe eventual progress/failure.
            handle.last_message = "startup_not_confirmed_yet"

        startup_note = ""
        if not started:
            startup_note = " Startup heartbeat not observed yet; check query_agent_status shortly."
        return ToolResult(
            output=(
                f"Spawned agent {agent_id} "
                f"(agent_id={agent_id}, task_id={agent_id}, pid={process.pid}). "
                f"Use query_agent_status/send_to_agent for heterogeneous agents.{startup_note}"
            ),
            metadata={
                "agent_id": agent_id,
                "task_id": agent_id,
                "agent_type": arguments.agent_type,
                "state_root": state_root_str,
            },
        )

