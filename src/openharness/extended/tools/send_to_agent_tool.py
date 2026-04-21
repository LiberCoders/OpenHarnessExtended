"""Tool for sending structured downlink messages to spawned agents."""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.extended.channel import get_channel_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class SendToAgentToolInput(BaseModel):
    """Arguments for structured downlink."""

    agent_id: str = Field(description="Target spawned agent id.")
    message_type: str = Field(default="instruction_append", description="instruction_append or terminate")
    text: str = Field(default="", description="Text payload for instruction_append.")


class SendToAgentTool(BaseTool):
    """Send one structured message to the spawned agent's channel."""

    name = "send_to_agent"
    description = "Send a structured command to a spawned heterogeneous agent."
    input_model = SendToAgentToolInput

    async def execute(self, arguments: SendToAgentToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        registry = get_channel_registry()
        handle = registry.get_by_agent_id(arguments.agent_id)
        if handle is None:
            return ToolResult(output=f"agent not found: {arguments.agent_id}", is_error=True)
        if not handle.process.is_alive():
            return ToolResult(output=f"agent is not running: {arguments.agent_id}", is_error=True)
        if arguments.message_type not in {"instruction_append", "terminate"}:
            return ToolResult(output="unsupported message_type", is_error=True)

        payload = {"text": arguments.text}
        if arguments.message_type == "terminate":
            payload["message"] = arguments.text or "terminated by leader"
        registry.send_downlink(handle, kind=arguments.message_type, payload=payload)
        return ToolResult(output=f"sent {arguments.message_type} to {arguments.agent_id}")

