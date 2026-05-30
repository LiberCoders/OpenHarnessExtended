"""Tool for querying delegated expert status from channel aggregate state."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, Field

from openharness.extended.channel import get_channel_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class GetExpertStatusToolInput(BaseModel):
    """Arguments for querying one delegated expert status."""

    expert_id: str = Field(
        ...,
        description=(
            "The expert id returned by delegate_to_expert (shown as "
            "expert_id=... in its output and metadata). Required."
        ),
    )
    wait: bool = Field(default=False, description="Wait until expert process exits.")
    timeout_seconds: float = Field(default=0.0, ge=0.0, le=3600.0)


class GetExpertStatusTool(BaseTool):
    """Read combined channel state + task lifecycle for one expert."""

    name = "get_expert_status"
    description = (
        "Query status of a delegated expert. You MUST pass expert_id "
        "(the id returned by delegate_to_expert)."
    )
    input_model = GetExpertStatusToolInput

    def is_read_only(self, arguments: GetExpertStatusToolInput) -> bool:
        return True

    async def execute(self, arguments: GetExpertStatusToolInput, context: ToolExecutionContext) -> ToolResult:
        registry = get_channel_registry()
        handle = registry.get_by_expert_id(arguments.expert_id)
        if handle is None:
            return ToolResult(output="expert not found in channel registry", is_error=True)

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
        # Internal only (not exposed): synthesizes result.status for the hard-crash
        # case where the worker died without writing/sending a terminal status.
        channel_status = handle.status
        if process_status == "stopped" and channel_status == "running":
            channel_status = "completed" if exit_code == 0 else "failed"
            handle.status = channel_status
        result_payload = _load_result_dict(handle.metadata.get("state_root"))
        if not result_payload:
            result_payload = _ensure_result_when_missing(
                handle,
                process_status=process_status,
                exit_code=exit_code,
                channel_status=channel_status,
            )
        new_events = registry.take_new_events(handle)
        # Only dynamic, query-specific state here. Static info (expert_id, task,
        # state_root, the path convention) was already delivered by
        # delegate_to_expert and lives in the conversation — repeating it on every
        # poll just wastes tokens. channel_status is omitted too: it is recoverable
        # from process_status (running/stopped) + result.status (terminal). Step
        # events are already dicts, so they embed directly — no manual formatter.
        data = {
            "process_status": process_status,
            "process_exit_code": exit_code,
            "last_action": handle.last_action,
            "last_message": handle.last_message,
            "last_screenshot": handle.last_screenshot,
            "result": result_payload,
            "new_steps_since_last_query": len(new_events),
            "new_steps": new_events,
        }
        return ToolResult(output=json.dumps(data, indent=2, ensure_ascii=False))


def _load_result_dict(state_root: str | object) -> dict:
    """Return the on-disk result.json as a dict, or {} if absent/unreadable."""
    if not isinstance(state_root, str) or not state_root.strip():
        return {}
    result_path = Path(state_root) / "result.json"
    if not result_path.is_file():
        return {}
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _ensure_result_when_missing(handle, *, process_status: str, exit_code: object, channel_status: str) -> dict:
    state_root = handle.metadata.get("state_root")
    if not isinstance(state_root, str) or not state_root.strip():
        return {}
    result_path = Path(state_root) / "result.json"
    if result_path.is_file():
        return _load_result_dict(state_root)
    if process_status != "stopped":
        return {}
    payload = {
        "expert_id": handle.expert_id,
        "status": "failed" if channel_status not in {"completed", "stopped"} else channel_status,
        "exit_reason": "expert_exited_without_result",
        "last_action": handle.last_action or "terminate(bootstrap_failure)",
        "message": handle.last_message or f"worker_stopped_without_result(exit_code={exit_code})",
        "last_screenshot": handle.last_screenshot or "",
        "steps": 0,
    }
    try:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return {}
    return payload

