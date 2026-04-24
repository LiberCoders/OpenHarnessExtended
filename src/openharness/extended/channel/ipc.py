"""Minimal duplex queue channel for leader/worker communication."""

from __future__ import annotations

import json
import os
import time
from multiprocessing import get_context
from queue import Empty
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openharness.config.paths import get_data_dir


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class ChannelMessage:
    """Structured channel message."""

    seq: int
    kind: str
    payload: dict[str, Any]
    timestamp: float
    direction: str

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "kind": self.kind,
                "payload": self.payload,
                "timestamp": self.timestamp,
                "direction": self.direction,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json_line(cls, line: str) -> "ChannelMessage":
        data = json.loads(line)
        timestamp = data.get("timestamp")
        return cls(
            seq=int(data["seq"]),
            kind=str(data["kind"]),
            payload=dict(data.get("payload") or {}),
            timestamp=float(_now() if timestamp is None else timestamp),
            direction=str(data.get("direction") or ""),
        )


class LeaderQueueChannel:
    """Leader side queue channel."""

    def __init__(self, downlink_queue: Any, uplink_queue: Any, *, transport: str = "queue") -> None:
        self._downlink_queue = downlink_queue
        self._uplink_queue = uplink_queue
        self._transport = transport
        self._downlink_seq = 0
        self._uplink_offset = 0

    @property
    def downlink_queue(self) -> Any:
        return self._downlink_queue

    @property
    def uplink_queue(self) -> Any:
        return self._uplink_queue

    def send_to_worker(self, kind: str, payload: dict[str, Any]) -> ChannelMessage:
        self._downlink_seq += 1
        msg = ChannelMessage(
            seq=self._downlink_seq,
            kind=kind,
            payload=payload,
            timestamp=_now(),
            direction="downlink",
        )
        _write_channel_line(self._transport, self._downlink_queue, msg.to_json_line())
        return msg

    def read_for_leader(self) -> list[ChannelMessage]:
        out, self._uplink_offset = _read_channel_messages(
            transport=self._transport,
            source=self._uplink_queue,
            expected_direction="uplink",
            offset=self._uplink_offset,
        )
        return out

    def close(self) -> None:
        return None

class WorkerQueueChannel:
    """Worker side queue channel."""

    def __init__(self, downlink_queue: Any, uplink_queue: Any, *, transport: str = "queue") -> None:
        self._downlink_queue = downlink_queue
        self._uplink_queue = uplink_queue
        self._transport = transport
        self._uplink_seq = 0
        self._downlink_offset = 0

    def send_to_leader(self, kind: str, payload: dict[str, Any]) -> ChannelMessage:
        self._uplink_seq += 1
        msg = ChannelMessage(
            seq=self._uplink_seq,
            kind=kind,
            payload=payload,
            timestamp=_now(),
            direction="uplink",
        )
        _write_channel_line(self._transport, self._uplink_queue, msg.to_json_line())
        return msg

    def read_for_worker(self) -> list[ChannelMessage]:
        out, self._downlink_offset = _read_channel_messages(
            transport=self._transport,
            source=self._downlink_queue,
            expected_direction="downlink",
            offset=self._downlink_offset,
        )
        return out

    def close(self) -> None:
        return None


def create_channel(expert_id: str) -> LeaderQueueChannel:
    """Create leader-side queue channel."""
    if os.name == "nt":
        channel_root = get_data_dir() / "extended" / "channels" / str(expert_id)
        channel_root.mkdir(parents=True, exist_ok=True)
        downlink = str((channel_root / "downlink.jsonl").resolve())
        uplink = str((channel_root / "uplink.jsonl").resolve())
        Path(downlink).touch(exist_ok=True)
        Path(uplink).touch(exist_ok=True)
        return LeaderQueueChannel(downlink_queue=downlink, uplink_queue=uplink, transport="file")
    ctx = get_context("spawn")
    downlink = ctx.Queue()
    uplink = ctx.Queue()
    return LeaderQueueChannel(downlink_queue=downlink, uplink_queue=uplink, transport="queue")


def connect_worker_channel(*, downlink_queue: Any, uplink_queue: Any) -> WorkerQueueChannel:
    """Connect worker to queue channel."""
    if isinstance(downlink_queue, str) and isinstance(uplink_queue, str):
        return WorkerQueueChannel(downlink_queue=downlink_queue, uplink_queue=uplink_queue, transport="file")
    return WorkerQueueChannel(downlink_queue=downlink_queue, uplink_queue=uplink_queue, transport="queue")


def _append_jsonl(path: str, line: str) -> None:
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _read_jsonl_since(path: str, offset: int) -> tuple[list[str], int]:
    file_path = Path(path)
    if not file_path.exists():
        return [], offset
    with file_path.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        content = handle.read()
        next_offset = handle.tell()
    if not content:
        return [], next_offset
    lines = [line for line in content.splitlines() if line.strip()]
    return lines, next_offset


def _write_channel_line(transport: str, target: Any, line: str) -> None:
    if transport == "queue":
        target.put(line)
        return
    _append_jsonl(target, line)


def _read_channel_messages(
    *,
    transport: str,
    source: Any,
    expected_direction: str,
    offset: int,
) -> tuple[list[ChannelMessage], int]:
    out: list[ChannelMessage] = []
    if transport == "queue":
        while True:
            try:
                line = source.get_nowait()
            except Empty:
                break
            try:
                msg = ChannelMessage.from_json_line(line)
            except json.JSONDecodeError:
                continue
            if msg.direction == expected_direction:
                out.append(msg)
        return out, offset

    lines, next_offset = _read_jsonl_since(source, offset)
    for line in lines:
        try:
            msg = ChannelMessage.from_json_line(line)
        except json.JSONDecodeError:
            continue
        if msg.direction == expected_direction:
            out.append(msg)
    return out, next_offset

