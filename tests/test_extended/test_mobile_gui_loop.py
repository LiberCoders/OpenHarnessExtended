"""Loop-level behavior for the mobile GUI step loop (soft-failure circuit breaker)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openharness.extended.engine.unified_loop import MAX_CONSECUTIVE_SOFT_FAILURES, ExpertLoopPack
from openharness.extended.experts.mobile_gui.action_executor import ActionExecutor
from openharness.extended.experts.mobile_gui.context import MobileGuiContext
from openharness.extended.experts.mobile_gui.device.results import DeviceActionResult, DeviceCommandResult
from openharness.extended.experts.mobile_gui.types import ActionType, GuiAction, Observation, ReasoningOutcome


class _FailingClickDriver:
    transport_name = "fake"

    async def click(self, x: int, y: int) -> DeviceActionResult:
        cmd = DeviceCommandResult(
            command=f"tap {x} {y}", exit_code=1, stdout="", stderr="device busy", ok=False
        )
        return DeviceActionResult(ok=False, results=[cmd])


class _FakeChannel:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def read_for_worker(self):
        return []

    def send_to_leader(self, kind, payload):
        if kind == "status":
            self.statuses.append(payload.get("status", ""))


class _FakeStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save_step(self, step, payload):  # noqa: D401 - test stub
        pass


class _FakePerception:
    async def observe(self, context):
        return Observation(screenshot_path="/tmp/does-not-matter.png")


class _ClickReasoning:
    """Always plans a single click — which the failing driver soft-rejects."""

    async def think(self, context, observation):
        action = GuiAction(action=ActionType.CLICK, x=10, y=20)
        return ReasoningOutcome(
            raw_response="click",
            reasoning_content="",
            model_request=None,
            parsed_actions=[action],
            adapted_actions=[action],
        )


@pytest.mark.asyncio
async def test_consecutive_soft_failures_trip_circuit_breaker(tmp_path: Path) -> None:
    """Repeated soft failures don't terminate immediately, but trip after N in a row."""
    context = MobileGuiContext(task="t")
    pack = ExpertLoopPack(
        config=SimpleNamespace(max_steps=10, expert_type="mobile_gui", cwd=str(tmp_path)),
        expert_id="exp-test",
        store=_FakeStore(tmp_path),
        context=context,
        perception=_FakePerception(),
        reasoning=_ClickReasoning(),
        executor=ActionExecutor(driver=_FailingClickDriver()),  # type: ignore[arg-type]
        backend=None,
        post_action_settle_seconds=0.0,
    )

    status, exit_reason = await pack.run_steps(_FakeChannel())

    assert status == "failed"
    assert exit_reason == "expert_failed_repeated_action_errors"
    # Tripped exactly on the Nth consecutive soft failure, not before / not at max_steps.
    assert context.step == MAX_CONSECUTIVE_SOFT_FAILURES
