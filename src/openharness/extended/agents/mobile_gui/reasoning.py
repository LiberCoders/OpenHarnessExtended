"""One-step GUI reasoning via GuiInferenceBackend.infer."""

from __future__ import annotations

from openharness.extended.agents.mobile_gui.backends.base import GuiInferenceBackend
from openharness.extended.agents.mobile_gui.context import MobileGuiContext
from openharness.extended.agents.mobile_gui.types import Observation, ReasoningOutcome


class MobileGuiReasoning:
    """Delegate each step to a vision / GUI backend (single model call per think)."""

    def __init__(self, backend: GuiInferenceBackend) -> None:
        self._backend = backend

    async def think(self, context: MobileGuiContext, observation: Observation) -> ReasoningOutcome:
        text, model_request = await self._backend.infer(context=context, observation=observation)
        parsed_action = self._backend.parse_action(text)
        adapted_action = self._backend.adapt_action(action=parsed_action, observation=observation)
        return ReasoningOutcome(
            raw_response=text,
            model_request=model_request,
            parsed_action=parsed_action,
            adapted_action=adapted_action,
        )
