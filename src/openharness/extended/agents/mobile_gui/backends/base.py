"""Base interface for mobile GUI inference backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from openharness.extended.agents.mobile_gui.context import MobileGuiContext
from openharness.extended.agents.mobile_gui.types import GuiAction, Observation


class GuiInferenceBackend(ABC):
    """Abstract backend used by mobile GUI reasoning."""

    @abstractmethod
    async def infer(
        self, *, context: MobileGuiContext, observation: Observation
    ) -> tuple[str, dict[str, Any] | None]:
        """Return raw model response text and serialized request payload."""

    @abstractmethod
    def parse_action(self, response_text: str) -> tuple[GuiAction, dict[str, Any] | None]:
        """Parse backend response text into an executable action."""
        raise NotImplementedError

    def adapt_action(self, *, action: GuiAction, observation: Observation) -> GuiAction:
        """Backend-specific action post-processing hook."""
        return action

    def close(self) -> None:
        """Optional cleanup hook."""
        return None

