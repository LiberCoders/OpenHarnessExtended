"""Leader-side in-memory registry for spawned experts."""

from __future__ import annotations

import gc
from dataclasses import dataclass, field
from typing import Any

from openharness.extended.channel.ipc import LeaderQueueChannel


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
            if message.kind != "status":
                continue
            payload = message.payload
            handle.status = str(payload.get("status") or handle.status)
            handle.last_action = str(payload.get("last_action") or handle.last_action)
            handle.last_message = str(payload.get("message") or handle.last_message)
            handle.last_screenshot = str(payload.get("last_screenshot") or handle.last_screenshot)
        return handle

    def send_downlink(self, handle: ExpertHandle, *, kind: str, payload: dict[str, Any]) -> None:
        handle.channel.send_to_worker(kind=kind, payload=payload)


_REGISTRY = ChannelRegistry()


def get_channel_registry() -> ChannelRegistry:
    return _REGISTRY

