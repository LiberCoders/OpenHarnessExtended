"""Tool for sending structured downlink messages to delegated experts."""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.extended.channel import get_channel_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class SendToExpertToolInput(BaseModel):
    """Arguments for structured downlink."""

    expert_id: str = Field(description="Target delegated expert id.")
    message_type: str = Field(default="instruction_append", description="instruction_append or terminate")
    text: str = Field(default="", description="Text payload for instruction_append.")


class SendToExpertTool(BaseTool):
    """Send one structured message to the delegated expert's channel."""

    name = "send_to_expert"
    description = "Send a structured command to a delegated expert."
    input_model = SendToExpertToolInput

    async def execute(self, arguments: SendToExpertToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        registry = get_channel_registry()
        handle = registry.get_by_expert_id(arguments.expert_id)
        if handle is None:
            return ToolResult(output=f"expert not found: {arguments.expert_id}", is_error=True)
        if not handle.process.is_alive():
            return ToolResult(output=f"expert is not running: {arguments.expert_id}", is_error=True)
        if arguments.message_type not in {"instruction_append", "terminate"}:
            return ToolResult(output="unsupported message_type", is_error=True)

        payload = {"text": arguments.text}
        if arguments.message_type == "terminate":
            payload["message"] = arguments.text or "terminated by leader"
        registry.send_downlink(handle, kind=arguments.message_type, payload=payload)
        return ToolResult(output=f"sent {arguments.message_type} to {arguments.expert_id}")

