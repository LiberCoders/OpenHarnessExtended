"""One-step GUI reasoning via GuiInferenceBackend.infer."""

from __future__ import annotations

from openharness.extended.experts.mobile_gui.backends.base import GuiInferenceBackend
from openharness.extended.experts.mobile_gui.context import MobileGuiContext
from openharness.extended.experts.mobile_gui.types import Observation, ReasoningOutcome


class MobileGuiReasoning:
    """Delegate each step to a vision / GUI backend (single model call per think)."""

    def __init__(self, backend: GuiInferenceBackend) -> None:
        self._backend = backend

    async def think(self, context: MobileGuiContext, observation: Observation) -> ReasoningOutcome:
        infer_result = await self._backend.infer(context=context, observation=observation)
        parsed_action = self._backend.parse_action(infer_result.raw_response)
        adapted_action = self._backend.adapt_action(action=parsed_action, observation=observation)
        return ReasoningOutcome(
            raw_response=infer_result.raw_response,
            reasoning_content=infer_result.reasoning_content,
            model_request=infer_result.model_request,
            parsed_action=parsed_action,
            adapted_action=adapted_action,
        )
