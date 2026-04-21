"""Heterogeneous agent workers: shared step loop + pack built per agent_type."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.config.paths import get_data_dir
from openharness.extended.agents.mobile_gui.action_executor import ActionExecutor
from openharness.extended.agents.mobile_gui.backends.registry import resolve_gui_backend
from openharness.extended.agents.mobile_gui.context import MobileGuiContext
from openharness.extended.agents.mobile_gui.device.hdc import HdcMobileDeviceDriver
from openharness.extended.agents.mobile_gui.perception import MobilePerception
from openharness.extended.agents.mobile_gui.reasoning import MobileGuiReasoning
from openharness.extended.agents.mobile_gui.state_store import MobileGuiStateStore
from openharness.extended.agents.mobile_gui.status_digest import build_status_digest
from openharness.extended.agents.mobile_gui.types import GuiAction
from openharness.extended.channel import connect_worker_channel
from openharness.ui.runtime import build_runtime, close_runtime, start_runtime

logger = logging.getLogger(__name__)

FALLBACK_AGENT_TYPE_SLUG = "unspecified"

WORKER_IMPLEMENTED_AGENT_TYPES: frozenset[str] = frozenset({"mobile_gui"})


def _default_capability_profile(agent_type: str) -> str:
    if agent_type == "mobile_gui":
        return "hdc_minimal_v1"
    return "default"


@dataclass
class HeterogeneousAgentRunConfig:
    """Spawned heterogeneous worker: common fields for all agent_type values."""

    agent_type: str
    agent_id: str
    task: str
    capability_profile: str
    max_steps: int
    device_serial: str | None
    cwd: str | None
    runtime_overrides: dict

    @classmethod
    def from_worker_dict(cls, raw: dict[str, Any], *, agent_type: str) -> HeterogeneousAgentRunConfig:
        cap = str(raw.get("capability_profile") or "").strip()
        if not cap:
            cap = _default_capability_profile(agent_type)
        return cls(
            agent_type=agent_type,
            agent_id=str(raw.get("agent_id") or ""),
            task=str(raw.get("task") or ""),
            capability_profile=cap,
            max_steps=int(raw.get("max_steps") or 8),
            device_serial=raw.get("device_serial"),
            cwd=raw.get("cwd"),
            runtime_overrides=dict(raw.get("runtime_overrides") or {}),
        )


def _resolve_agent_id(config: HeterogeneousAgentRunConfig) -> str:
    fallback_ts = datetime.now().strftime("%Y%m%d%H%M%S")
    type_slug = (config.agent_type or "").strip() or FALLBACK_AGENT_TYPE_SLUG
    return config.agent_id or f"{type_slug}_{fallback_ts}_{uuid4().hex[:6]}"


@dataclass
class HeterogeneousAgentLoopPack:
    """Everything the shared perceive→think→act step loop needs for one worker run."""

    config: HeterogeneousAgentRunConfig
    agent_id: str
    store: MobileGuiStateStore
    context: Any
    perception: Any
    reasoning: Any
    executor: Any
    backend: Any | None

    async def run_steps(self, channel) -> str:
        """Generic max_steps loop: downlink, observe, think, act, persist, status uplink."""
        cfg = self.config
        status = "running"
        logger.info("%s loop start: agent_id=%s max_steps=%s", cfg.agent_type, self.agent_id, cfg.max_steps)

        for step in range(1, max(1, cfg.max_steps) + 1):
            self.context.step = step
            downlink = channel.read_for_worker()
            for message in downlink:
                if message.kind == "instruction_append":
                    extra = str(message.payload.get("text") or "").strip()
                    if extra:
                        self.context.extra_instruction = f"{self.context.extra_instruction}\n{extra}".strip()
                if message.kind == "terminate":
                    self.context.done = True
                    self.context.last_action = "terminate(external)"
                    self.context.last_message = str(message.payload.get("message") or "terminated by leader")
            if self.context.done:
                status = "killed"
                break

            observation = await self.perception.observe(self.context)
            self.context.last_screenshot = observation.screenshot_path
            outcome = await self.reasoning.think(self.context, observation)
            action = outcome.adapted_action
            await self.executor.execute(action, self.context)

            step_payload = {
                "step": self.context.step,
                "message": self.context.last_message,
                "screenshot_path": self.context.last_screenshot,
                "raw_response": outcome.raw_response,
                "parsed_action": _action_to_dict(outcome.parsed_action),
                "adapted_action": _action_to_dict(outcome.adapted_action),
                "model_request": _truncate_model_request(outcome.model_request),
            }
            self.store.save_step(step, step_payload)
            logger.info(
                "%s step done: agent_id=%s step=%s action=%s message=%s",
                cfg.agent_type,
                self.agent_id,
                step,
                self.context.last_action,
                self.context.last_message,
            )
            if self.context.done:
                status = "completed" if (action.status or "success") == "success" else "failed"
            channel.send_to_leader("status", build_status_digest(self.context, status=status))
            if self.context.done:
                break
        else:
            status = "completed"
        return status


def build_heterogeneous_loop_pack(
    config: HeterogeneousAgentRunConfig,
    *,
    agent_id: str,
    bundle: Any,
) -> HeterogeneousAgentLoopPack:
    """Assemble store, context, and per–agent_type drivers in one place."""
    root = get_data_dir() / "extended" / "agents" / agent_id
    runtime_overrides = config.runtime_overrides or {}

    if config.agent_type == "mobile_gui":
        store = MobileGuiStateStore(root)
        context = MobileGuiContext(task=config.task)
        driver = HdcMobileDeviceDriver(cwd=Path(config.cwd or ".").resolve(), serial=config.device_serial)
        perception = MobilePerception(driver=driver, steps_dir=store.steps_dir)
        settings = bundle.current_settings()
        type_section = getattr(settings, config.agent_type, None) if settings is not None else None
        gui_backend_config = runtime_overrides.get("gui_backend")
        if gui_backend_config is None and isinstance(type_section, dict):
            gui_backend_config = type_section.get("gui_backend")
        gui_cfg = gui_backend_config if isinstance(gui_backend_config, dict) else None
        backend = resolve_gui_backend(
            gui_backend_config=gui_cfg,
        )
        reasoning = MobileGuiReasoning(backend)
        executor = ActionExecutor(driver=driver)
        return HeterogeneousAgentLoopPack(
            config=config,
            agent_id=agent_id,
            store=store,
            context=context,
            perception=perception,
            reasoning=reasoning,
            executor=executor,
            backend=backend,
        )

    raise RuntimeError(f"no loop pack builder for agent_type={config.agent_type!r}")


async def _run_heterogeneous_agent_loop(config: HeterogeneousAgentRunConfig, channel) -> int:
    """Harness runtime, then one pack + shared step runner."""
    agent_id = _resolve_agent_id(config)
    bundle = None
    pack: HeterogeneousAgentLoopPack | None = None
    status = "running"
    try:
        if config.agent_type not in WORKER_IMPLEMENTED_AGENT_TYPES:
            raise RuntimeError(f"worker loop not implemented for agent_type={config.agent_type!r}")

        logger.info("%s worker init: agent_id=%s task=%s", config.agent_type, agent_id, config.task)

        runtime_overrides = config.runtime_overrides or {}
        logger.info("%s build_runtime start: agent_id=%s", config.agent_type, agent_id)
        bundle = await build_runtime(
            cwd=config.cwd,
            model=runtime_overrides.get("model"),
            max_turns=runtime_overrides.get("max_turns"),
            base_url=runtime_overrides.get("base_url"),
            system_prompt=runtime_overrides.get("system_prompt"),
            api_key=runtime_overrides.get("api_key"),
            api_format=runtime_overrides.get("api_format"),
            active_profile=runtime_overrides.get("active_profile"),
            permission_mode=runtime_overrides.get("permission_mode"),
        )
        logger.info("%s build_runtime done: agent_id=%s", config.agent_type, agent_id)
        logger.info("%s start_runtime start: agent_id=%s", config.agent_type, agent_id)
        await start_runtime(bundle)
        logger.info("%s start_runtime done: agent_id=%s", config.agent_type, agent_id)

        pack = build_heterogeneous_loop_pack(config, agent_id=agent_id, bundle=bundle)
        pack.store.save_meta(
            {
                "agent_id": agent_id,
                "agent_type": config.agent_type,
                "task": config.task,
                "capability_profile": config.capability_profile,
                "runtime_overrides": config.runtime_overrides,
            }
        )

        status = await pack.run_steps(channel)

    except BaseException as exc:
        status = "failed"
        if pack is not None and pack.context is not None:
            pack.context.last_action = "terminate(exception)"
            message = str(exc).strip()
            if not message:
                message = exc.__class__.__name__
            if isinstance(exc, SystemExit):
                message = (
                    f"worker exited early with SystemExit(code={exc.code!r}). "
                    "Please check runtime auth/model configuration."
                )
            pack.context.last_message = message
        logger.exception(
            "%s worker failed: agent_id=%s error=%s",
            config.agent_type,
            agent_id,
            str(exc).strip() or type(exc).__name__,
        )
    finally:
        logger.info("%s worker finalize: agent_id=%s status=%s", config.agent_type, agent_id, status)
        ctx = pack.context if pack is not None else None
        st = pack.store if pack is not None else None
        if st is not None:
            st.save_result(
                {
                    "agent_id": agent_id,
                    "status": status,
                    "last_action": getattr(ctx, "last_action", "") if ctx else "",
                    "message": getattr(ctx, "last_message", "") if ctx else "",
                    "last_screenshot": getattr(ctx, "last_screenshot", "") if ctx else "",
                    "steps": getattr(ctx, "step", 0) if ctx else 0,
                }
            )
        if pack is not None and pack.backend is not None:
            try:
                pack.backend.close()
            except Exception:
                pass
        if ctx is not None:
            try:
                channel.send_to_leader("status", build_status_digest(ctx, status=status))
            except Exception:
                pass
        try:
            channel.close()
        except Exception:
            pass
        if bundle is not None:
            try:
                await close_runtime(bundle)
            except Exception:
                pass
    return 0 if status in {"completed", "killed"} else 1


def run_heterogeneous_worker_entry(config: dict, downlink_queue, uplink_queue) -> None:
    """Multiprocessing/subprocess entry: validate agent_type and run the shared heterogeneous loop."""
    env_overrides = config.get("env_overrides")
    if isinstance(env_overrides, dict):
        for key in ("OPENHARNESS_CONFIG_DIR", "OPENHARNESS_DATA_DIR", "OPENHARNESS_LOGS_DIR"):
            value = env_overrides.get(key)
            if isinstance(value, str) and value.strip():
                os.environ[key] = value.strip()
    worker_channel = connect_worker_channel(
        downlink_queue=downlink_queue,
        uplink_queue=uplink_queue,
    )
    agent_type = str(config.get("agent_type") or "").strip()
    if not agent_type:
        logger.error("heterogeneous worker: missing config['agent_type']")
        raise SystemExit(2)
    if agent_type not in WORKER_IMPLEMENTED_AGENT_TYPES:
        logger.error("heterogeneous worker: unsupported agent_type=%r", agent_type)
        raise SystemExit(2)

    parsed = HeterogeneousAgentRunConfig.from_worker_dict(config, agent_type=agent_type)
    exit_code = asyncio.run(_run_heterogeneous_agent_loop(parsed, worker_channel))
    raise SystemExit(exit_code)


def _action_to_dict(action: GuiAction) -> dict:
    return {
        "action": action.action,
        "x": action.x,
        "y": action.y,
        "seconds": action.seconds,
        "status": action.status,
        "message": action.message,
    }


def _truncate_model_request(payload: object) -> object:
    if isinstance(payload, dict):
        return {key: _truncate_model_request(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_truncate_model_request(item) for item in payload]
    if isinstance(payload, str):
        return _truncate_base64_data_url(payload)
    return payload


def _truncate_base64_data_url(value: str, *, keep_head: int = 80, keep_tail: int = 80) -> str:
    marker = ";base64,"
    marker_idx = value.find(marker)
    if marker_idx < 0:
        return value
    payload_start = marker_idx + len(marker)
    b64 = value[payload_start:]
    if len(b64) <= keep_head + keep_tail + 32:
        return value
    prefix = value[:payload_start]
    return f"{prefix}{b64[:keep_head]}...<omitted:{len(b64) - keep_head - keep_tail}>...{b64[-keep_tail:]}"
