"""Tool for querying heterogeneous agent status from channel aggregate state."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, Field

from openharness.extended.channel import get_channel_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class QueryAgentStatusToolInput(BaseModel):
    """Arguments for querying one spawned agent status."""

    agent_id: str | None = Field(default=None)
    task_id: str | None = Field(default=None)
    wait: bool = Field(default=False, description="Wait until agent process exits.")
    timeout_seconds: float = Field(default=0.0, ge=0.0, le=3600.0)


class QueryAgentStatusTool(BaseTool):
    """Read combined channel state + task lifecycle for one agent."""

    name = "query_agent_status"
    description = "Query status of a spawned heterogeneous agent by agent_id or task_id."
    input_model = QueryAgentStatusToolInput

    def is_read_only(self, arguments: QueryAgentStatusToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: QueryAgentStatusToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        registry = get_channel_registry()
        handle = None
        if arguments.agent_id:
            handle = registry.get_by_agent_id(arguments.agent_id)
        elif arguments.task_id:
            handle = registry.get_by_task_id(arguments.task_id)
        else:
            return ToolResult(output="agent_id or task_id is required", is_error=True)
        if handle is None:
            return ToolResult(output="agent not found in channel registry", is_error=True)

        if arguments.wait:
            deadline = asyncio.get_running_loop().time() + arguments.timeout_seconds
            while True:
                handle = registry.refresh(handle)
                if not handle.process.is_alive():
                    break
                if arguments.timeout_seconds > 0 and asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(0.2)
        handle = registry.refresh(handle)
        process = handle.process
        process_status = "running" if process.is_alive() else "stopped"
        exit_code = process.exitcode if process.exitcode is not None else "none"
        channel_status = handle.status
        if process_status == "stopped" and channel_status == "running":
            channel_status = "completed" if exit_code == 0 else "failed"
            handle.status = channel_status
        result_payload = _load_result(handle.metadata.get("state_root"))
        if result_payload == "{}":
            result_payload = _ensure_result_when_missing(
                handle,
                process_status=process_status,
                exit_code=exit_code,
                channel_status=channel_status,
            )
        return ToolResult(
            output=(
                f"agent_id={handle.agent_id}\n"
                f"agent_type={handle.agent_type}\n"
                f"task_id={handle.task_id}\n"
                f"process_status={process_status}\n"
                f"process_exit_code={exit_code}\n"
                f"channel_status={channel_status}\n"
                f"last_action={handle.last_action}\n"
                f"last_message={handle.last_message}\n"
                f"last_screenshot={handle.last_screenshot}\n"
                f"result={result_payload}"
            )
        )


def _load_result(state_root: str | object) -> str:
    if not isinstance(state_root, str) or not state_root.strip():
        return "{}"
    result_path = Path(state_root) / "result.json"
    if not result_path.is_file():
        return "{}"
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "{}"
    return json.dumps(data, ensure_ascii=False)


def _ensure_result_when_missing(handle, *, process_status: str, exit_code: object, channel_status: str) -> str:
    state_root = handle.metadata.get("state_root")
    if not isinstance(state_root, str) or not state_root.strip():
        return "{}"
    result_path = Path(state_root) / "result.json"
    if result_path.is_file():
        return _load_result(state_root)
    if process_status != "stopped":
        return "{}"
    payload = {
        "agent_id": handle.agent_id,
        "status": "failed" if channel_status not in {"completed", "killed"} else channel_status,
        "last_action": handle.last_action or "terminate(bootstrap_failure)",
        "message": handle.last_message or f"worker_stopped_without_result(exit_code={exit_code})",
        "last_screenshot": handle.last_screenshot or "",
        "steps": 0,
    }
    try:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return "{}"
    return json.dumps(payload, ensure_ascii=False)

