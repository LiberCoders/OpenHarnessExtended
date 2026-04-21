"""Leader-side in-memory registry for spawned heterogeneous agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openharness.extended.channel.ipc import LeaderQueueChannel


@dataclass
class AgentHandle:
    """Runtime tracking data for one spawned heterogeneous agent."""

    agent_id: str
    agent_type: str
    task_id: str
    channel: LeaderQueueChannel
    process: Any
    task: str
    status: str = "running"
    last_action: str = ""
    last_message: str = ""
    last_screenshot: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ChannelRegistry:
    """Small registry used by spawn/query/send tools."""

    def __init__(self) -> None:
        self._handles: dict[str, AgentHandle] = {}
        self._task_to_agent: dict[str, str] = {}

    def register(self, handle: AgentHandle) -> None:
        self._handles[handle.agent_id] = handle
        self._task_to_agent[handle.task_id] = handle.agent_id

    def get_by_agent_id(self, agent_id: str) -> AgentHandle | None:
        return self._handles.get(agent_id)

    def get_by_task_id(self, task_id: str) -> AgentHandle | None:
        agent_id = self._task_to_agent.get(task_id)
        if not agent_id:
            return None
        return self._handles.get(agent_id)

    def refresh(self, handle: AgentHandle) -> AgentHandle:
        for message in handle.channel.read_for_leader():
            if message.kind != "status":
                continue
            payload = message.payload
            handle.status = str(payload.get("status") or handle.status)
            handle.last_action = str(payload.get("last_action") or handle.last_action)
            handle.last_message = str(payload.get("message") or handle.last_message)
            handle.last_screenshot = str(payload.get("last_screenshot") or handle.last_screenshot)
        return handle

    def send_downlink(self, handle: AgentHandle, *, kind: str, payload: dict[str, Any]) -> None:
        handle.channel.send_to_worker(kind=kind, payload=payload)


_REGISTRY = ChannelRegistry()


def get_channel_registry() -> ChannelRegistry:
    return _REGISTRY

