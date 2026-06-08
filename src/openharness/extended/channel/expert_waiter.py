"""Re-wake the main conversation loop while background experts are running.

When the main loop tries to exit (no tool calls returned), the active_work_checker
callback fires.  If any experts are still alive, we wait here until either:
  - at least one expert terminates, OR
  - EXPERT_EVENT_THRESHOLD new step events have accumulated across all experts
Then we return a short notification message so run_query injects it and
continues the loop.  The model can then decide to call get_expert_status or
do other work.

Crucially, we only call registry.refresh() (drain IPC → handle.events) but
never call take_new_events() — events remain available for the model to read
via get_expert_status.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

EXPERT_EVENT_THRESHOLD = 5
_POLL_INTERVAL = 2.0


def make_expert_waiter(
    event_threshold: int = EXPERT_EVENT_THRESHOLD,
    poll_interval: float = _POLL_INTERVAL,
) -> Callable[[], Awaitable[str | None]]:
    """Return an async callable suitable for QueryContext.active_work_checker."""

    async def check_and_wait() -> str | None:
        from openharness.extended.channel.registry import get_channel_registry

        registry = get_channel_registry()

        running = []
        for handle in list(registry._handles.values()):
            proc = getattr(handle, "process", None)
            if proc is None:
                continue
            try:
                alive = proc.is_alive()
            except Exception:
                alive = False
            if alive and handle.status == "running":
                running.append(handle)

        if not running:
            return None

        log.debug(
            "active_work_checker: %d expert(s) still running — blocking main loop exit",
            len(running),
        )

        while True:
            await asyncio.sleep(poll_interval)

            total_pending = 0
            terminated_ids: list[str] = []

            for handle in running:
                registry.refresh(handle)  # drain IPC → handle.events (no cursor advance)
                total_pending += len(handle.events) - handle.events_cursor

                proc = getattr(handle, "process", None)
                terminated = False
                if proc is not None:
                    try:
                        terminated = not proc.is_alive()
                    except Exception:
                        terminated = True
                if not terminated and handle.status in ("completed", "failed", "stopped"):
                    terminated = True
                if terminated:
                    terminated_ids.append(handle.expert_id)

            if terminated_ids or total_pending >= event_threshold:
                break

        expert_ids = [h.expert_id for h in running]
        log.debug(
            "active_work_checker: waking main loop (experts=%s, pending_events=%d, terminated=%s)",
            expert_ids,
            total_pending,
            terminated_ids,
        )
        if terminated_ids:
            msg = (
                f"[System: Expert(s) {', '.join(terminated_ids)} have just finished. "
                f"Call get_expert_status to review their results if needed.]"
            )
        else:
            msg = (
                f"[System: Expert(s) {', '.join(expert_ids)} are still running "
                f"and have accumulated {total_pending} new step(s) since last check. "
                f"Call get_expert_status if you want to review their current progress.]"
            )
        return msg

    return check_and_wait
