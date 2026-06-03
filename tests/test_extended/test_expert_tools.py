"""Tests for minimal expert tools."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from openharness.extended.channel import ExpertHandle, connect_worker_channel, create_channel, get_channel_registry
from openharness.extended.tools.get_expert_status_tool import (
    GetExpertStatusTool,
    GetExpertStatusToolInput,
)
from openharness.extended.tools.send_to_expert_tool import SendToExpertTool, SendToExpertToolInput
from openharness.extended.tools.delegate_to_expert_tool import (
    DelegateToExpertTool,
    DelegateToExpertToolInput,
    OPENHARNESS_PARENT_SETTINGS_KEY,
)
from openharness.tools.base import ToolExecutionContext


def _emit_startup_heartbeat(channel) -> None:
    worker = connect_worker_channel(
        downlink_queue=channel.downlink_queue,
        uplink_queue=channel.uplink_queue,
    )
    worker.send_to_leader(
        "status",
        {
            "status": "running",
            "step": "0",
            "last_action": "startup:init",
            "message": "init",
            "last_screenshot": "",
        },
    )
    worker.close()


@dataclass
class _FakePermission:
    mode: str


@dataclass
class _FakeSettings:
    active_profile: str = "debug-openai-local"
    model: str = "deepseek-v3.2"
    base_url: str = "https://127.0.0.1:5678/v1"
    api_format: str = "openai"
    permission: object = _FakePermission(mode="default")
    mobile_gui: dict = None

    def __post_init__(self) -> None:
        if self.mobile_gui is None:
            self.mobile_gui = {"gui_backend": {"type": "gui_plus", "model": "gui-plus-2026-02-26"}}


@pytest.mark.asyncio
async def test_delegate_to_expert_registers_handle_and_returns_ids(tmp_path: Path, monkeypatch):
    class _FakeProcess:
        pid = 43210
        exitcode = None

        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(
        DelegateToExpertTool,
        "_spawn_worker",
        lambda self, config, channel: (_emit_startup_heartbeat(channel), _FakeProcess())[1],
    )
    monkeypatch.setattr(
        "openharness.extended.tools.delegate_to_expert_tool.load_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))

    tool = DelegateToExpertTool()
    result = await tool.execute(
        DelegateToExpertToolInput(expert_type="mobile_gui", task="打开设置"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is False
    assert "expert_id=mobile_gui_" in result.output
    assert "task_id=mobile_gui_" in result.output
    assert "pid=43210" in result.output


@pytest.mark.asyncio
async def test_delegate_to_expert_inherits_parent_runtime_settings(tmp_path: Path, monkeypatch):
    captured_config: dict[str, object] = {}

    class _FakeProcess:
        pid = 10001
        exitcode = None

        def is_alive(self) -> bool:
            return True

    def _fake_spawn(self, config, channel):  # noqa: ANN001
        captured_config.update(config)
        _emit_startup_heartbeat(channel)
        return _FakeProcess()

    monkeypatch.setattr(DelegateToExpertTool, "_spawn_worker", _fake_spawn)
    monkeypatch.setattr(
        "openharness.extended.tools.delegate_to_expert_tool.load_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))

    tool = DelegateToExpertTool()
    result = await tool.execute(
        DelegateToExpertToolInput(expert_type="mobile_gui", task="打开应用市场"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is False

    runtime_overrides = captured_config["runtime_overrides"]
    assert isinstance(runtime_overrides, dict)
    assert runtime_overrides["active_profile"] == "debug-openai-local"
    assert runtime_overrides["model"] == "deepseek-v3.2"
    assert runtime_overrides["api_format"] == "openai"
    assert "gui_backend" not in runtime_overrides
    snap = runtime_overrides[OPENHARNESS_PARENT_SETTINGS_KEY]
    assert isinstance(snap, dict)
    assert snap["model"] == "deepseek-v3.2"
    assert snap["mobile_gui"]["gui_backend"]["type"] == "gui_plus"
    env_overrides = captured_config["env_overrides"]
    assert isinstance(env_overrides, dict)
    assert env_overrides["OPENHARNESS_DATA_DIR"] == str((tmp_path / "data").resolve())


@pytest.mark.asyncio
async def test_delegate_to_expert_keeps_explicit_runtime_overrides(tmp_path: Path, monkeypatch):
    captured_config: dict[str, object] = {}

    class _FakeProcess:
        pid = 10002
        exitcode = None

        def is_alive(self) -> bool:
            return True

    def _fake_spawn(self, config, channel):  # noqa: ANN001
        captured_config.update(config)
        _emit_startup_heartbeat(channel)
        return _FakeProcess()

    monkeypatch.setattr(DelegateToExpertTool, "_spawn_worker", _fake_spawn)
    monkeypatch.setattr(
        "openharness.extended.tools.delegate_to_expert_tool.load_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))

    tool = DelegateToExpertTool()
    result = await tool.execute(
        DelegateToExpertToolInput(
            expert_type="mobile_gui",
            task="打开应用市场",
            runtime_overrides={"gui_backend": {"type": "gui_plus", "model": "gui-plus-2026-02-26"}},
        ),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is False

    runtime_overrides = captured_config["runtime_overrides"]
    assert isinstance(runtime_overrides, dict)
    assert runtime_overrides["gui_backend"] == {"type": "gui_plus", "model": "gui-plus-2026-02-26"}
    snap = runtime_overrides[OPENHARNESS_PARENT_SETTINGS_KEY]
    assert snap["mobile_gui"]["gui_backend"]["type"] == "gui_plus"


@pytest.mark.asyncio
async def test_query_and_send_tools_use_channel_registry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    registry = get_channel_registry()
    channel = create_channel("mobile_gui_test")

    class _FakeProcess:
        exitcode = None

        def is_alive(self) -> bool:
            return True

    registry.register(
        ExpertHandle(
            expert_id="mobile_gui_test",
            expert_type="mobile_gui",
            task_id="b7654321",
            channel=channel,
            process=_FakeProcess(),
            task="test",
        )
    )

    send_tool = SendToExpertTool()
    send_result = await send_tool.execute(
        SendToExpertToolInput(expert_id="mobile_gui_test", message_type="instruction_append", text="继续"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert send_result.is_error is False

    worker = connect_worker_channel(
        downlink_queue=channel.downlink_queue,
        uplink_queue=channel.uplink_queue,
    )
    # Simulate worker uplink status.
    worker.send_to_leader(
        "status",
        {
            "status": "running",
            "last_action": "wait(1.0)",
            "message": "ok",
            "last_screenshot": "a.jpeg",
        },
    )
    await asyncio.sleep(0.05)
    query_tool = GetExpertStatusTool()
    query_result = await query_tool.execute(
        GetExpertStatusToolInput(expert_id="mobile_gui_test"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert query_result.is_error is False
    payload = json.loads(query_result.output)
    assert payload["process_status"] == "running"
    assert payload["last_action"] == "wait(1.0)"
    worker.close()


@pytest.mark.asyncio
async def test_request_stop_all_sends_cooperative_terminate(tmp_path: Path, monkeypatch):
    """request_stop_all enqueues a downlink `terminate` for live workers only."""
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    registry = get_channel_registry()

    class _Alive:
        def is_alive(self) -> bool:
            return True

    class _Dead:
        def is_alive(self) -> bool:
            return False

    alive_channel = create_channel("stop_alive")
    dead_channel = create_channel("stop_dead")
    registry.register(
        ExpertHandle(
            expert_id="stop_alive",
            expert_type="mobile_gui",
            task_id="a",
            channel=alive_channel,
            process=_Alive(),
            task="t",
        )
    )
    registry.register(
        ExpertHandle(
            expert_id="stop_dead",
            expert_type="mobile_gui",
            task_id="d",
            channel=dead_channel,
            process=_Dead(),
            task="t",
        )
    )

    registry.request_stop_all(message="bye")
    await asyncio.sleep(0.05)

    alive_worker = connect_worker_channel(
        downlink_queue=alive_channel.downlink_queue, uplink_queue=alive_channel.uplink_queue
    )
    dead_worker = connect_worker_channel(
        downlink_queue=dead_channel.downlink_queue, uplink_queue=dead_channel.uplink_queue
    )
    alive_msgs = alive_worker.read_for_worker()
    dead_msgs = dead_worker.read_for_worker()
    # Live worker gets the cooperative terminate; the dead one is skipped entirely.
    assert any(m.kind == "terminate" and m.payload.get("message") == "bye" for m in alive_msgs)
    assert all(m.kind != "terminate" for m in dead_msgs)
    alive_worker.close()
    dead_worker.close()

