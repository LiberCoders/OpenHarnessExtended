"""Leader-side in-memory registry for spawned experts."""

from __future__ import annotations

import gc
import logging
from dataclasses import dataclass, field
from typing import Any

from openharness.extended.channel.ipc import LeaderQueueChannel

logger = logging.getLogger(__name__)


@dataclass
class ExpertHandle:
    """Runtime tracking data for one spawned expert."""

    expert_id: str
    expert_type: str
    task_id: str
    channel: LeaderQueueChannel
    process: Any
    task: str
    status: str = "running"
    last_action: str = ""
    last_message: str = ""
    last_screenshot: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    # Full per-step trajectory pushed by the worker over the channel. ``events``
    # accumulates every step; ``events_cursor`` marks how many have already been
    # delivered to the leader (main conversation), so each query returns only the
    # unseen tail.
    events: list[dict[str, Any]] = field(default_factory=list)
    events_cursor: int = 0


class ChannelRegistry:
    """Small registry used by spawn/query/send tools."""

    def __init__(self) -> None:
        self._handles: dict[str, ExpertHandle] = {}

    def close_all_channels(self) -> None:
        """Close leader-side IPC for every registered expert.

        ``Queue.close()`` alone does not release the queue's three internal SemLocks
        (``_sem``, ``_rlock``, ``_wlock``) from ``multiprocessing.resource_tracker``;
        that only happens when the SemLock is garbage-collected (its
        ``util.Finalize`` callback calls ``sem_unlink`` + ``resource_tracker.unregister``).
        So we drop every reference path and force a GC sweep here. Without this, the
        SIGTERM the React frontend sends shortly after we emit ``shutdown`` skips
        interpreter cleanup and ``resource_tracker`` prints
        "There appear to be N leaked semaphore objects to clean up at shutdown".
        """
        for handle in list(self._handles.values()):
            try:
                handle.channel.close()
            except Exception:
                pass
        # Drop every reference path to the queues so SemLock weakrefs can fire.
        self._handles.clear()
        # Force a collection to run the SemLock Finalize callbacks now,
        # while the resource_tracker pipe is still open.
        gc.collect()

    def register(self, handle: ExpertHandle) -> None:
        self._handles[handle.expert_id] = handle

    def get_by_expert_id(self, expert_id: str) -> ExpertHandle | None:
        return self._handles.get(expert_id)

    def refresh(self, handle: ExpertHandle) -> ExpertHandle:
        for message in handle.channel.read_for_leader():
            if message.kind == "step":
                # Accumulate (never collapse) so the full trajectory is preserved
                # for incremental delivery via take_new_events().
                handle.events.append(dict(message.payload))
            elif message.kind == "status":
                payload = message.payload
                handle.status = str(payload.get("status") or handle.status)
                handle.last_action = str(payload.get("last_action") or handle.last_action)
                handle.last_message = str(payload.get("message") or handle.last_message)
                handle.last_screenshot = str(payload.get("last_screenshot") or handle.last_screenshot)
            else:
                # Unknown uplink kind: NOT surfaced to the main conversation. Log it
                # (rather than silently dropping) so a newly-added worker message
                # type without a handler here is visible and easy to wire up.
                logger.warning(
                    "expert %s: unhandled uplink message kind=%r dropped — not delivered to the "
                    "main conversation; add a branch in ChannelRegistry.refresh to surface it.",
                    handle.expert_id,
                    message.kind,
                )
        return handle

    def take_new_events(self, handle: ExpertHandle) -> list[dict[str, Any]]:
        """Return step events not yet delivered to the leader, advancing the cursor.

        Once returned, events are considered consumed (placed into the main
        conversation's context) and are never handed out again.
        """
        new_events = handle.events[handle.events_cursor:]
        handle.events_cursor = len(handle.events)
        return new_events

    def send_downlink(self, handle: ExpertHandle, *, kind: str, payload: dict[str, Any]) -> None:
        handle.channel.send_to_worker(kind=kind, payload=payload)


_REGISTRY = ChannelRegistry()


def get_channel_registry() -> ChannelRegistry:
    return _REGISTRY

