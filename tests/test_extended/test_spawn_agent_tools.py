"""Tests for minimal heterogeneous agent tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from openharness.extended.channel import AgentHandle, connect_worker_channel, create_channel, get_channel_registry
from openharness.extended.tools.query_agent_status_tool import (
    QueryAgentStatusTool,
    QueryAgentStatusToolInput,
)
from openharness.extended.tools.send_to_agent_tool import SendToAgentTool, SendToAgentToolInput
from openharness.extended.tools.spawn_agent_tool import (
    OPENHARNESS_PARENT_SETTINGS_KEY,
    SpawnAgentTool,
    SpawnAgentToolInput,
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
async def test_spawn_agent_registers_handle_and_returns_ids(tmp_path: Path, monkeypatch):
    class _FakeProcess:
        pid = 43210
        exitcode = None

        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(
        SpawnAgentTool,
        "_spawn_worker",
        lambda self, config, channel: (_emit_startup_heartbeat(channel), _FakeProcess())[1],
    )
    monkeypatch.setattr(
        "openharness.extended.tools.spawn_agent_tool.load_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))

    tool = SpawnAgentTool()
    result = await tool.execute(
        SpawnAgentToolInput(agent_type="mobile_gui", task="打开设置"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is False
    assert "agent_id=mobile_gui_" in result.output
    assert "task_id=mobile_gui_" in result.output
    assert "pid=43210" in result.output


@pytest.mark.asyncio
async def test_spawn_agent_inherits_parent_runtime_settings(tmp_path: Path, monkeypatch):
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

    monkeypatch.setattr(SpawnAgentTool, "_spawn_worker", _fake_spawn)
    monkeypatch.setattr(
        "openharness.extended.tools.spawn_agent_tool.load_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))

    tool = SpawnAgentTool()
    result = await tool.execute(
        SpawnAgentToolInput(agent_type="mobile_gui", task="打开应用市场"),
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
async def test_spawn_agent_keeps_explicit_runtime_overrides(tmp_path: Path, monkeypatch):
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

    monkeypatch.setattr(SpawnAgentTool, "_spawn_worker", _fake_spawn)
    monkeypatch.setattr(
        "openharness.extended.tools.spawn_agent_tool.load_settings",
        lambda: _FakeSettings(),
    )
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))

    tool = SpawnAgentTool()
    result = await tool.execute(
        SpawnAgentToolInput(
            agent_type="mobile_gui",
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
        AgentHandle(
            agent_id="mobile_gui_test",
            agent_type="mobile_gui",
            task_id="b7654321",
            channel=channel,
            process=_FakeProcess(),
            task="test",
        )
    )

    send_tool = SendToAgentTool()
    send_result = await send_tool.execute(
        SendToAgentToolInput(agent_id="mobile_gui_test", message_type="instruction_append", text="继续"),
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
    query_tool = QueryAgentStatusTool()
    query_result = await query_tool.execute(
        QueryAgentStatusToolInput(agent_id="mobile_gui_test"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert query_result.is_error is False
    assert "channel_status=running" in query_result.output
    assert "process_status=running" in query_result.output
    assert "last_action=wait(1.0)" in query_result.output
    worker.close()

