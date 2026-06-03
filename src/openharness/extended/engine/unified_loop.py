"""Expert workers: shared step loop + pack built per expert_type."""

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
from openharness.extended.experts.mobile_gui.action_executor import ActionExecutor
from openharness.extended.experts.mobile_gui.backends.registry import resolve_gui_backend
from openharness.extended.experts.mobile_gui.context import MobileGuiContext
from openharness.extended.experts.mobile_gui.device import create_mobile_driver
from openharness.extended.experts.mobile_gui.device.base import UnsupportedActionError
from openharness.extended.experts.mobile_gui.device.package_aliases import register_task_apps
from openharness.extended.experts.mobile_gui.perception import MobilePerception
from openharness.extended.experts.mobile_gui.reasoning import MobileGuiReasoning
from openharness.extended.experts.mobile_gui.state_store import MobileGuiStateStore
from openharness.extended.experts.mobile_gui.status_digest import build_status_digest, build_step_event
from openharness.extended.experts.mobile_gui.types import ActionType
from openharness.extended.channel import connect_worker_channel
from openharness.ui.runtime import build_runtime, close_runtime, start_runtime

logger = logging.getLogger(__name__)

FALLBACK_EXPERT_TYPE_SLUG = "unspecified"
_DEFAULT_POST_ACTION_SETTLE_SECONDS = 0.35
# Consecutive soft failures (device-reported ok=False) tolerated before the
# loop force-terminates the expert. Hard failures terminate immediately.
MAX_CONSECUTIVE_SOFT_FAILURES = 3
# Action types that don't touch the screen — no settle delay needed afterwards.
_NON_DEVICE_ACTIONS = frozenset({ActionType.WAIT, ActionType.TERMINATE, ActionType.INTERACT})

WORKER_IMPLEMENTED_EXPERT_TYPES: frozenset[str] = frozenset({"mobile_gui"})


def _default_capability_profile(expert_type: str) -> str:
    if expert_type == "mobile_gui":
        return "hdc_minimal_v1"
    return "default"


# Must match OPENHARNESS_PARENT_SETTINGS_KEY in delegate_to_expert_tool (avoid import cycle).
_OPENHARNESS_PARENT_SETTINGS_SNAPSHOT = "openharness_parent_settings"


def _merge_mobile_gui_section(
    runtime_overrides: dict[str, Any],
    settings_expert_section: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge `mobile_gui` from parent snapshot, settings, and spawn-time overrides (later wins)."""
    merged: dict[str, Any] = {}
    snap = runtime_overrides.get(_OPENHARNESS_PARENT_SETTINGS_SNAPSHOT)
    if isinstance(snap, dict):
        parent_mg = snap.get("mobile_gui")
        if isinstance(parent_mg, dict):
            merged.update(parent_mg)
    if isinstance(settings_expert_section, dict):
        merged.update(settings_expert_section)
    ro_mg = runtime_overrides.get("mobile_gui")
    if isinstance(ro_mg, dict):
        merged.update(ro_mg)
    return merged


def _coalesce_device_serial(*candidates: object) -> str | None:
    """First non-empty string wins (spawn arg before settings `mobile_gui.device_serial`)."""
    for raw in candidates:
        if raw is None:
            continue
        s = str(raw).strip()
        if s:
            return s
    return None


@dataclass
class ExpertRunConfig:
    """Spawned expert worker: common fields for all expert_type values."""

    expert_type: str
    expert_id: str
    task: str
    capability_profile: str
    max_steps: int
    device_serial: str | None
    cwd: str | None
    runtime_overrides: dict

    @classmethod
    def from_worker_dict(cls, raw: dict[str, Any], *, expert_type: str) -> ExpertRunConfig:
        cap = str(raw.get("capability_profile") or "").strip()
        if not cap:
            cap = _default_capability_profile(expert_type)
        return cls(
            expert_type=expert_type,
            expert_id=str(raw.get("expert_id") or ""),
            task=str(raw.get("task") or ""),
            capability_profile=cap,
            max_steps=int(raw.get("max_steps") or 8),
            device_serial=raw.get("device_serial"),
            cwd=raw.get("cwd"),
            runtime_overrides=dict(raw.get("runtime_overrides") or {}),
        )


def _resolve_expert_id(config: ExpertRunConfig) -> str:
    fallback_ts = datetime.now().strftime("%Y%m%d%H%M%S")
    type_slug = (config.expert_type or "").strip() or FALLBACK_EXPERT_TYPE_SLUG
    return config.expert_id or f"{type_slug}_{fallback_ts}_{uuid4().hex[:6]}"


def _to_non_negative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return max(0.0, default)
    return max(0.0, parsed)


@dataclass
class ExpertLoopPack:
    """Everything the shared perceive→think→act step loop needs for one worker run."""

    config: ExpertRunConfig
    expert_id: str
    store: MobileGuiStateStore
    context: Any
    perception: Any
    reasoning: Any
    executor: Any
    backend: Any | None
    post_action_settle_seconds: float = 0.0

    async def run_steps(self, channel) -> tuple[str, str]:
        """Generic max_steps loop: downlink, observe, think, act, persist, status uplink.

        Returns ``(status, exit_reason)``. ``exit_reason`` is a generic,
        expert-agnostic classification of *why* the loop ended — computed from
        loop-level signals (context.done, action.status, step exhaustion) rather
        than any expert's own last_action vocabulary, so it stays meaningful when
        a different expert type reuses this loop.
        """
        cfg = self.config
        status = "running"
        exit_reason = ""
        consecutive_soft_failures = 0
        logger.info("%s loop start: expert_id=%s max_steps=%s", cfg.expert_type, self.expert_id, cfg.max_steps)

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
                status = "stopped"
                exit_reason = "expert_killed_by_leader"
                break

            observation = await self.perception.observe(self.context)
            self.context.last_screenshot = observation.screenshot_path
            outcome = await self.reasoning.think(self.context, observation)
            actions = outcome.adapted_actions
            # A driver primitive the transport can't do raises out of run() and
            # is handled as a hard failure by the outer except (terminate).
            results = await self.executor.run(actions, self.context)

            did_device_action = any(a.action not in _NON_DEVICE_ACTIONS for a in actions)
            if not self.context.done and did_device_action and self.post_action_settle_seconds > 0:
                await asyncio.sleep(self.post_action_settle_seconds)

            step_payload = {
                "step": self.context.step,
                "message": self.context.last_message,
                "screenshot_path": self.context.last_screenshot,
                "raw_response": outcome.raw_response,
                "reasoning_content": outcome.reasoning_content,
                "parsed_actions": [a.to_dict() for a in outcome.parsed_actions],
                "adapted_actions": [a.to_dict() for a in outcome.adapted_actions],
                "action_results": [r.to_dict() for r in results],
                "model_request": _truncate_model_request(outcome.model_request),
            }
            self.store.save_step(step, step_payload)
            logger.info(
                "%s step done: expert_id=%s step=%s action=%s message=%s",
                cfg.expert_type,
                self.expert_id,
                step,
                self.context.last_action,
                self.context.last_message,
            )
            if self.context.done:
                # The backend declared completion via a terminate action.
                term = next((a for a in actions if a.action == ActionType.TERMINATE), None)
                declared = (term.status or "").strip().lower() if term and term.status else ""
                if declared == "success":
                    status, exit_reason = "completed", "expert_completed"
                elif declared == "failure":
                    status, exit_reason = "failed", "expert_failed"
                elif not declared:
                    # Expert ended but gave no verdict — don't claim success.
                    status, exit_reason = "stopped", "expert_done_unspecified"
                else:
                    # Expert ended with a non-standard status (e.g. it wants user
                    # interaction). The raw value is already visible to the leader
                    # via last_action/step action.status, so the reason stays
                    # generic rather than echoing it.
                    status, exit_reason = "stopped", "expert_done_others"
            elif any(not r.device_result.ok for r in results):
                # Soft failure: a device command reported ok=False but didn't
                # crash. Let the backend retry next step; trip a breaker after
                # MAX_CONSECUTIVE_SOFT_FAILURES in a row.
                consecutive_soft_failures += 1
                if consecutive_soft_failures >= MAX_CONSECUTIVE_SOFT_FAILURES:
                    self.context.done = True
                    status, exit_reason = "failed", "expert_failed_repeated_action_errors"
            else:
                consecutive_soft_failures = 0
            # Per-step trajectory (text + screenshot path) so the leader can replay
            # the full execution; the terminal/summary state still rides on "status".
            channel.send_to_leader(
                "step",
                build_step_event(
                    step_payload,
                    last_action=self.context.last_action,
                    status=status,
                    state_root=self.store.root,
                    base_dir=cfg.cwd,
                ),
            )
            channel.send_to_leader("status", build_status_digest(self.context, status=status))
            if self.context.done:
                break
        else:
            # Loop hit the step ceiling before the agent declared done — the task
            # is NOT completed, only truncated. "stopped" = ended without a
            # success/failure verdict (shared with the no/other-verdict cases).
            status = "stopped"
            exit_reason = "max_steps_reached"
        return status, exit_reason


def build_expert_loop_pack(
    config: ExpertRunConfig,
    *,
    expert_id: str,
    bundle: Any,
) -> ExpertLoopPack:
    """Assemble store, context, and per–expert_type drivers in one place."""
    root = get_data_dir() / "extended" / "experts" / expert_id
    runtime_overrides = config.runtime_overrides or {}

    if config.expert_type == "mobile_gui":
        store = MobileGuiStateStore(root)
        context = MobileGuiContext(task=config.task)
        cwd = Path(config.cwd or ".").resolve()
        settings = bundle.current_settings()
        type_section = getattr(settings, config.expert_type, None) if settings is not None else None
        mobile_gui_opts = _merge_mobile_gui_section(
            runtime_overrides,
            type_section if isinstance(type_section, dict) else None,
        )
        # Small settle delay between "action executed" and next screenshot.
        # Purpose: absorb UI commit/animation latency to reduce stale-frame captures
        # after tap/swipe/navigation. This is transport-agnostic (adb/hdc both issue
        # input commands before UI rendering is fully stable), so we keep one shared
        # default and allow overriding via mobile_gui.post_action_settle_seconds.
        post_action_settle_seconds = _to_non_negative_float(
            mobile_gui_opts.get("post_action_settle_seconds"),
            default=_DEFAULT_POST_ACTION_SETTLE_SECONDS,
        )
        # prompt_meta is the canonical, top-level location for per-task metadata
        # (apps, today) supplied by the caller via settings. Forwarded to the
        # GUI backend below so it can ground its prompts.
        raw_prompt_meta = getattr(settings, "prompt_meta", None) if settings is not None else None
        prompt_meta = raw_prompt_meta if isinstance(raw_prompt_meta, dict) else {}
        task_apps = prompt_meta.get("apps")
        if isinstance(task_apps, list) and task_apps:
            register_task_apps(task_apps)
        transport = str(mobile_gui_opts.get("device_transport") or "").strip().lower()
        spawn_serial = _coalesce_device_serial(config.device_serial)
        settings_serial = _coalesce_device_serial(mobile_gui_opts.get("device_serial"))
        serial = _coalesce_device_serial(config.device_serial, mobile_gui_opts.get("device_serial"))
        if spawn_serial and settings_serial and spawn_serial != settings_serial:
            logger.info(
                "%s device_serial: spawn argument %r overrides merged mobile_gui.device_serial %r "
                "(expert_id=%s)",
                config.expert_type,
                spawn_serial,
                settings_serial,
                expert_id,
            )
        driver = create_mobile_driver(
            transport=transport,
            cwd=cwd,
            serial=serial,
            options=mobile_gui_opts,
        )
        perception = MobilePerception(driver=driver, steps_dir=store.steps_dir)
        gui_backend_config = runtime_overrides.get("gui_backend")
        if gui_backend_config is None and isinstance(type_section, dict):
            gui_backend_config = type_section.get("gui_backend")
        if gui_backend_config is None:
            gui_backend_config = mobile_gui_opts.get("gui_backend")
        gui_cfg: dict[str, Any] = dict(gui_backend_config) if isinstance(gui_backend_config, dict) else {}
        # Forward top-level prompt_meta into the backend config so the backend
        # (e.g. GuiPlusBackend reads ``today``) can see it without each backend
        # needing its own settings access. Backend-local prompt_meta wins on key
        # collisions for explicit per-backend overrides.
        if prompt_meta:
            merged_meta = {**prompt_meta, **(gui_cfg.get("prompt_meta") or {})}
            gui_cfg["prompt_meta"] = merged_meta
        backend = resolve_gui_backend(
            gui_backend_config=gui_cfg or None,
        )
        reasoning = MobileGuiReasoning(backend)
        executor = ActionExecutor(driver=driver)
        return ExpertLoopPack(
            config=config,
            expert_id=expert_id,
            store=store,
            context=context,
            perception=perception,
            reasoning=reasoning,
            executor=executor,
            backend=backend,
            post_action_settle_seconds=post_action_settle_seconds,
        )

    raise RuntimeError(f"no loop pack builder for expert_type={config.expert_type!r}")


async def _run_expert_loop(config: ExpertRunConfig, channel) -> int:
    """Harness runtime, then one pack + shared step runner."""
    expert_id = _resolve_expert_id(config)
    bundle = None
    pack: ExpertLoopPack | None = None
    status = "running"
    exit_reason = ""
    error_message = ""
    try:
        if config.expert_type not in WORKER_IMPLEMENTED_EXPERT_TYPES:
            raise RuntimeError(f"worker loop not implemented for expert_type={config.expert_type!r}")

        logger.info("%s worker init: expert_id=%s task=%s", config.expert_type, expert_id, config.task)

        runtime_overrides = config.runtime_overrides or {}
        logger.info("%s build_runtime start: expert_id=%s", config.expert_type, expert_id)
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
        logger.info("%s build_runtime done: expert_id=%s", config.expert_type, expert_id)
        logger.info("%s start_runtime start: expert_id=%s", config.expert_type, expert_id)
        await start_runtime(bundle)
        logger.info("%s start_runtime done: expert_id=%s", config.expert_type, expert_id)

        pack = build_expert_loop_pack(config, expert_id=expert_id, bundle=bundle)
        pack.store.save_meta(
            {
                "expert_id": expert_id,
                "expert_type": config.expert_type,
                "task": config.task,
                "capability_profile": config.capability_profile,
                "runtime_overrides": config.runtime_overrides,
            }
        )

        status, exit_reason = await pack.run_steps(channel)

    except (KeyboardInterrupt, asyncio.CancelledError):
        # Defensive: the cooperative stop path is the downlink "terminate" the
        # loop reads at each step boundary. But if an OS signal still reaches the
        # worker directly (e.g. a terminal Ctrl-C delivered to the whole process
        # group), unwind it as a clean, intentional stop — not a crash — so it
        # doesn't fall through to the failed/error branch below.
        status = "stopped"
        exit_reason = "expert_interrupted"
        if pack is not None and pack.context is not None:
            pack.context.last_action = "terminate(interrupted)"
            pack.context.last_message = "interrupted (Ctrl-C / terminate signal)"
        logger.info(
            "%s worker interrupted: expert_id=%s — flushing and cleaning up",
            config.expert_type,
            expert_id,
        )
    except BaseException as exc:
        status = "failed"
        # Distinguish a crash during init (auth/model/backend config) from one
        # mid-run; both are expert-agnostic and carry detail in error_message.
        if isinstance(exc, UnsupportedActionError) and pack is not None:
            # Hard failure: a backend asked for a primitive this transport can't do.
            exit_reason = "expert_unsupported_action"
        else:
            exit_reason = "error" if pack is not None else "bootstrap_error"
        # Always capture the specific reason, even on bootstrap failures
        # (build_runtime / start_runtime / pack build, incl. backend __init__)
        # where ``pack`` is still None. The finally block forwards this to the
        # leader so the main conversation sees *why* the expert died.
        message = str(exc).strip() or exc.__class__.__name__
        if isinstance(exc, SystemExit):
            message = (
                f"worker exited early with SystemExit(code={exc.code!r}). "
                "Please check runtime auth/model/backend configuration."
            )
        error_message = f"{type(exc).__name__}: {message}"
        if pack is not None and pack.context is not None:
            pack.context.last_action = "terminate(exception)"
            pack.context.last_message = error_message
        logger.exception(
            "%s worker failed: expert_id=%s error=%s",
            config.expert_type,
            expert_id,
            error_message,
        )
    finally:
        logger.info("%s worker finalize: expert_id=%s status=%s", config.expert_type, expert_id, status)
        ctx = pack.context if pack is not None else None
        st = pack.store if pack is not None else None
        # Bootstrap failure: no pack/store, so result.json would otherwise never
        # be written and get_expert_status would only show a generic
        # "worker_stopped_without_result". Create the store at the canonical
        # state root so the specific reason lands where the leader reads it.
        if st is None and status not in {"completed", "stopped"}:
            try:
                st = MobileGuiStateStore(get_data_dir() / "extended" / "experts" / expert_id)
            except Exception:
                st = None
        if st is not None:
            st.save_result(
                {
                    "expert_id": expert_id,
                    "status": status,
                    "exit_reason": exit_reason or "unknown",
                    "last_action": getattr(ctx, "last_action", "") if ctx else "terminate(bootstrap_failure)",
                    "message": getattr(ctx, "last_message", "") if ctx else error_message,
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
        elif error_message:
            # No context (bootstrap failure): synthesize a digest so the leader
            # still receives the specific reason over the channel.
            try:
                channel.send_to_leader(
                    "status",
                    {
                        "status": status,
                        "step": "0",
                        "last_action": "terminate(bootstrap_failure)",
                        "message": error_message,
                        "last_screenshot": "",
                    },
                )
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
    # completed/stopped are clean loop conclusions (no crash; killed folds into
    # stopped); only failures/errors exit non-zero. The task outcome itself lives
    # in result.status.
    return 0 if status in {"completed", "stopped"} else 1


def run_expert_worker_entry(config: dict, downlink_queue, uplink_queue) -> None:
    """Multiprocessing/subprocess entry: validate expert_type and run the shared expert loop."""
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
    expert_type = str(config.get("expert_type") or "").strip()
    if not expert_type:
        logger.error("expert worker: missing config['expert_type']")
        raise SystemExit(2)
    if expert_type not in WORKER_IMPLEMENTED_EXPERT_TYPES:
        logger.error("expert worker: unsupported expert_type=%r", expert_type)
        raise SystemExit(2)

    parsed = ExpertRunConfig.from_worker_dict(config, expert_type=expert_type)
    try:
        exit_code = asyncio.run(_run_expert_loop(parsed, worker_channel))
    except KeyboardInterrupt:
        # A signal that landed outside the loop's own handling (e.g. between
        # asyncio.run's task cancellation and return) bubbled up here. The loop's
        # finally already flushed and cleaned up; exit cleanly instead of dumping
        # a traceback. 130 = 128 + SIGINT, the conventional "interrupted" code.
        exit_code = 130
    raise SystemExit(exit_code)


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
