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


class _OkWaitDriver:
    transport_name = "fake"

    async def wait(self, seconds: float) -> DeviceActionResult:
        return DeviceActionResult(ok=True, results=[])


class _CountingWaitReasoning:
    """Plans one (successful, non-device) wait per step and counts think() calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def think(self, context, observation):
        self.calls += 1
        action = GuiAction(action=ActionType.WAIT, seconds=0.0)
        return ReasoningOutcome(
            raw_response="wait",
            reasoning_content="",
            model_request=None,
            parsed_actions=[action],
            adapted_actions=[action],
        )


class _TerminateOnSecondReadChannel:
    """Delivers a downlink `terminate` only when the loop reads at step 2's top."""

    def __init__(self) -> None:
        self.reads = 0
        self.statuses: list[str] = []

    def read_for_worker(self):
        self.reads += 1
        if self.reads >= 2:
            return [SimpleNamespace(kind="terminate", payload={"message": "stop"})]
        return []

    def send_to_leader(self, kind, payload):
        if kind == "status":
            self.statuses.append(payload.get("status", ""))


@pytest.mark.asyncio
async def test_downlink_terminate_stops_at_next_step_boundary(tmp_path: Path) -> None:
    """A terminate that arrives mid-run lets the in-flight step finish, then stops.

    This is the cooperative-stop contract the Ctrl-C fix relies on: the leader
    enqueues `terminate`; the worker completes the current step's reasoning/action
    and breaks *before* doing any work in the next step — it does not march on.
    """
    context = MobileGuiContext(task="t")
    reasoning = _CountingWaitReasoning()
    pack = ExpertLoopPack(
        config=SimpleNamespace(max_steps=10, expert_type="mobile_gui", cwd=str(tmp_path)),
        expert_id="exp-stop",
        store=_FakeStore(tmp_path),
        context=context,
        perception=_FakePerception(),
        reasoning=reasoning,
        executor=ActionExecutor(driver=_OkWaitDriver()),  # type: ignore[arg-type]
        backend=None,
        post_action_settle_seconds=0.0,
    )

    status, exit_reason = await pack.run_steps(_TerminateOnSecondReadChannel())

    assert status == "stopped"
    assert exit_reason == "expert_killed_by_leader"
    # Step 1 fully executed (one think); step 2 read the terminate at its top and
    # broke before think — so exactly one reasoning call, stopped at step 2.
    assert reasoning.calls == 1
    assert context.step == 2


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
