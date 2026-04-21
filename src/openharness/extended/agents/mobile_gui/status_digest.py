"""Build lightweight status digests for leader queries."""

from __future__ import annotations

from openharness.extended.agents.mobile_gui.context import MobileGuiContext


def build_status_digest(context: MobileGuiContext, *, status: str) -> dict[str, str]:
    """Return the normalized status payload published over the channel."""
    return {
        "status": status,
        "step": str(context.step),
        "last_action": context.last_action,
        "message": context.last_message,
        "last_screenshot": context.last_screenshot,
    }

