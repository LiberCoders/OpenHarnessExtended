"""Channel helpers for heterogeneous agents."""

from openharness.extended.channel.ipc import LeaderQueueChannel, WorkerQueueChannel, connect_worker_channel, create_channel
from openharness.extended.channel.registry import AgentHandle, get_channel_registry

__all__ = [
    "AgentHandle",
    "LeaderQueueChannel",
    "WorkerQueueChannel",
    "connect_worker_channel",
    "create_channel",
    "get_channel_registry",
]

