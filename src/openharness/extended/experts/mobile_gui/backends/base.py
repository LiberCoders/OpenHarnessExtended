"""Base interface for mobile GUI inference backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from openharness.extended.experts.mobile_gui.context import MobileGuiContext
from openharness.extended.experts.mobile_gui.types import GuiAction, InferResult, Observation


class GuiInferenceBackend(ABC):
    """Abstract backend used by mobile GUI reasoning."""

    @abstractmethod
    async def infer(
        self, *, context: MobileGuiContext, observation: Observation
    ) -> InferResult:
        """Return raw text, request payload, and reasoning content."""

    @abstractmethod
    def parse_action(self, response_text: str) -> list[GuiAction]:
        """Parse backend response text into one or more executable actions.

        Returns a list to allow composite actions (e.g. click + type) and
        arbitrary name remapping (e.g. a model 'click' that is really a swipe).
        The mapping from the model's native action space onto the canonical
        ``ActionType`` vocabulary lives entirely here.
        """
        raise NotImplementedError

    def adapt_action(
        self, *, actions: list[GuiAction], observation: Observation
    ) -> list[GuiAction]:
        """Backend-specific post-processing (e.g. coordinate normalization).

        Must return actions whose coordinates are device pixels — the executor
        passes them straight to the driver. Default is a no-op pass-through.
        """
        return actions

    def close(self) -> None:
        """Optional cleanup hook."""
        return None

