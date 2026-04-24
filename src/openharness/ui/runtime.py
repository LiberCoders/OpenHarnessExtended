"""Shared runtime assembly for headless and Textual UIs."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from openharness.api.client import AnthropicApiClient, SupportsStreamingMessages
from openharness.api.codex_client import CodexApiClient
from openharness.api.copilot_client import CopilotClient
from openharness.api.openai_client import OpenAICompatibleClient
from openharness.api.provider import auth_status, detect_provider
from openharness.bridge import get_bridge_manager
from openharness.commands import CommandContext, CommandResult, create_default_command_registry
from openharness.config import get_config_file_path, load_settings
from openharness.engine import QueryEngine
from openharness.engine.messages import (
    ConversationMessage,
    ToolResultBlock,
    ToolUseBlock,
    sanitize_conversation_messages,
)
from openharness.engine.query import MaxTurnsExceeded
from openharness.engine.stream_events import StreamEvent
from openharness.hooks import HookEvent, HookExecutionContext, HookExecutor, load_hook_registry
from openharness.hooks.hot_reload import HookReloader
from openharness.mcp.client import McpClientManager
from openharness.mcp.config import load_mcp_server_configs
from openharness.permissions import PermissionChecker
from openharness.plugins import load_plugins
from openharness.prompts import build_runtime_system_prompt
from openharness.state import AppState, AppStateStore
from openharness.services.session_backend import DEFAULT_SESSION_BACKEND, SessionBackend
from openharness.tools import ToolRegistry, create_default_tool_registry
from openharness.keybindings import load_keybindings

PermissionPrompt = Callable[[str, str], Awaitable[bool]]
AskUserPrompt = Callable[[str], Awaitable[str]]
SystemPrinter = Callable[[str], Awaitable[None]]
StreamRenderer = Callable[[StreamEvent], Awaitable[None]]
ClearHandler = Callable[[], Awaitable[None]]


@dataclass
class RuntimeBundle:
    """Shared runtime objects for one interactive session."""

    api_client: SupportsStreamingMessages
    cwd: str
    mcp_manager: McpClientManager
    tool_registry: ToolRegistry
    app_state: AppStateStore
    hook_executor: HookExecutor
    engine: QueryEngine
    commands: object
    external_api_client: bool
    enforce_max_turns: bool = True
    session_id: str = ""
    settings_overrides: dict[str, Any] = field(default_factory=dict)
    session_backend: SessionBackend = DEFAULT_SESSION_BACKEND
    extra_skill_dirs: tuple[str, ...] = ()
    extra_plugin_roots: tuple[str, ...] = ()

    def current_settings(self):
        """Return the effective settings for this session.

        We persist most settings to disk (``~/.openharness/settings.json``), but
        CLI options like ``--model``/``--api-format`` should remain in effect for
        the lifetime of the running process. Without this overlay, issuing any
        slash command (e.g. ``/fast``) would refresh UI state from disk and
        "snap back" the model/provider to whatever is stored in the config file.
        """
        return load_settings().merge_cli_overrides(**self.settings_overrides)

    def current_plugins(self):
        """Return currently visible plugins for the working tree."""
        return load_plugins(
            self.current_settings(),
            self.cwd,
            extra_roots=self.extra_plugin_roots,
        )

    def hook_summary(self) -> str:
        """Return the current hook summary."""
        return load_hook_registry(self.current_settings(), self.current_plugins()).summary()

    def plugin_summary(self) -> str:
        """Return the current plugin summary."""
        plugins = self.current_plugins()
        if not plugins:
            return "No plugins discovered."
        lines = ["Plugins:"]
        for plugin in plugins:
            state = "enabled" if plugin.enabled else "disabled"
            lines.append(f"- {plugin.manifest.name} [{state}] {plugin.manifest.description}")
        return "\n".join(lines)

    def mcp_summary(self) -> str:
        """Return the current MCP summary."""
        statuses = self.mcp_manager.list_statuses()
        if not statuses:
            return "No MCP servers configured."
        lines = ["MCP servers:"]
        for status in statuses:
            suffix = f" - {status.detail}" if status.detail else ""
            lines.append(f"- {status.name}: {status.state}{suffix}")
            if status.tools:
                lines.append(f"  tools: {', '.join(tool.name for tool in status.tools)}")
            if status.resources:
                lines.append(f"  resources: {', '.join(resource.uri for resource in status.resources)}")
        return "\n".join(lines)


def _resolve_api_client_from_settings(settings) -> SupportsStreamingMessages:
    """Build the appropriate API client for the resolved settings."""
    # Ensure profile fields (base_url, model, api_format) are projected to settings
    settings = settings.materialize_active_profile()

    def _safe_resolve_auth():
        try:
            return settings.resolve_auth()
        except (ValueError, Exception):
            print(
                "Error: No API key configured.\n"
                "  Run `oh auth login` to set up authentication, or set the\n"
                "  ANTHROPIC_API_KEY (or OPENAI_API_KEY) environment variable.",
                file=sys.stderr,
            )
            raise SystemExit(1)

    if settings.api_format == "copilot":
        from openharness.api.copilot_client import COPILOT_DEFAULT_MODEL

        copilot_model = (
            COPILOT_DEFAULT_MODEL
            if settings.model in {"claude-sonnet-4-20250514", "claude-sonnet-4-6", "sonnet", "default"}
            else settings.model
        )
        return CopilotClient(model=copilot_model)
    if settings.provider == "openai_codex":
        auth = _safe_resolve_auth()
        return CodexApiClient(
            auth_token=auth.value,
            base_url=settings.base_url,
        )
    if settings.provider == "anthropic_claude":
        return AnthropicApiClient(
            auth_token=_safe_resolve_auth().value,
            base_url=settings.base_url,
            claude_oauth=True,
            auth_token_resolver=lambda: settings.resolve_auth().value,
        )
    if settings.api_format in ("openai", "openai_compat"):
        auth = _safe_resolve_auth()
        return OpenAICompatibleClient(
            api_key=auth.value,
            base_url=settings.base_url,
            timeout=settings.timeout,
            tls_verify=settings.tls_verify,
        )
    auth = _safe_resolve_auth()
    return AnthropicApiClient(
        api_key=auth.value,
        base_url=settings.base_url,
    )


async def build_runtime(
    *,
    prompt: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    api_format: str | None = None,
    active_profile: str | None = None,
    api_client: SupportsStreamingMessages | None = None,
    permission_prompt: PermissionPrompt | None = None,
    ask_user_prompt: AskUserPrompt | None = None,
    restore_messages: list[dict] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
    enforce_max_turns: bool = True,
    session_backend: SessionBackend | None = None,
    permission_mode: str | None = None,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
) -> RuntimeBundle:
    """Build the shared runtime for an OpenHarness session."""
    settings_overrides: dict[str, Any] = {
        "model": model,
        "max_turns": max_turns,
        "base_url": base_url,
        "system_prompt": system_prompt,
        "api_key": api_key,
        "api_format": api_format,
        "active_profile": active_profile,
        "permission_mode": permission_mode,
    }
    settings = load_settings().merge_cli_overrides(**settings_overrides)
    cwd = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
    normalized_skill_dirs = tuple(str(Path(path).expanduser().resolve()) for path in (extra_skill_dirs or ()))
    normalized_plugin_roots = tuple(str(Path(path).expanduser().resolve()) for path in (extra_plugin_roots or ()))
    plugins = load_plugins(settings, cwd, extra_roots=normalized_plugin_roots)
    if api_client:
        resolved_api_client = api_client
    else:
        resolved_api_client = _resolve_api_client_from_settings(settings)
    mcp_manager = McpClientManager(load_mcp_server_configs(settings, plugins))
    await mcp_manager.connect_all()
    tool_registry = create_default_tool_registry(mcp_manager)
    provider = detect_provider(settings)
    bridge_manager = get_bridge_manager()
    app_state = AppStateStore(
        AppState(
            # Show the effective runtime model (after CLI/env/profile merges),
            # not profile.last_model which may be stale.
            model=settings.model,
            permission_mode=settings.permission.mode.value,
            theme=settings.theme,
            cwd=cwd,
            provider=provider.name,
            auth_status=auth_status(settings),
            base_url=settings.base_url or "",
            vim_enabled=settings.vim_mode,
            voice_enabled=settings.voice_mode,
            voice_available=provider.voice_supported,
            voice_reason=provider.voice_reason,
            fast_mode=settings.fast_mode,
            effort=settings.effort,
            passes=settings.passes,
            mcp_connected=sum(1 for status in mcp_manager.list_statuses() if status.state == "connected"),
            mcp_failed=sum(1 for status in mcp_manager.list_statuses() if status.state == "failed"),
            bridge_sessions=len(bridge_manager.list_sessions()),
            output_style=settings.output_style,
            keybindings=load_keybindings(),
        )
    )
    hook_reloader = HookReloader(get_config_file_path())
    hook_executor = HookExecutor(
        hook_reloader.current_registry() if api_client is None else load_hook_registry(settings, plugins),
        HookExecutionContext(
            cwd=Path(cwd).resolve(),
            api_client=resolved_api_client,
            default_model=settings.model,
        ),
    )
    engine_max_turns = settings.max_turns if (enforce_max_turns or max_turns is not None) else None
    system_prompt_text = build_runtime_system_prompt(
        settings,
        cwd=cwd,
        latest_user_prompt=prompt,
        extra_skill_dirs=normalized_skill_dirs,
        extra_plugin_roots=normalized_plugin_roots,
    )
    from uuid import uuid4

    session_id = uuid4().hex[:12]

    restored_metadata = {
        "permission_mode": settings.permission.mode.value,
        "read_file_state": [],
        "invoked_skills": [],
        "async_agent_state": [],
        "async_agent_tasks": [],
        "recent_work_log": [],
        "recent_verified_work": [],
        "task_focus_state": {
            "goal": "",
            "recent_goals": [],
            "active_artifacts": [],
            "verified_state": [],
            "next_step": "",
        },
        "compact_checkpoints": [],
    }
    if isinstance(restore_tool_metadata, dict):
        for key, value in restore_tool_metadata.items():
            restored_metadata[key] = value

    engine = QueryEngine(
        api_client=resolved_api_client,
        tool_registry=tool_registry,
        permission_checker=PermissionChecker(settings.permission),
        cwd=cwd,
        model=settings.model,
        system_prompt=system_prompt_text,
        max_tokens=settings.max_tokens,
        context_window_tokens=settings.context_window_tokens or settings.memory.context_window_tokens,
        auto_compact_threshold_tokens=(
            settings.auto_compact_threshold_tokens
            or settings.memory.auto_compact_threshold_tokens
        ),
        max_turns=engine_max_turns,
        permission_prompt=permission_prompt,
        ask_user_prompt=ask_user_prompt,
        hook_executor=hook_executor,
        tool_metadata={
            "mcp_manager": mcp_manager,
            "bridge_manager": bridge_manager,
            "extra_skill_dirs": normalized_skill_dirs,
            "extra_plugin_roots": normalized_plugin_roots,
            "session_id": session_id,
            **restored_metadata,
        },
    )
    # Restore messages from a saved session if provided
    if restore_messages:
        restored = sanitize_conversation_messages(
            [ConversationMessage.model_validate(m) for m in restore_messages]
        )
        engine.load_messages(restored)

    # Start Docker sandbox if configured
    if settings.sandbox.enabled and settings.sandbox.backend == "docker":
        from openharness.sandbox.session import start_docker_sandbox

        await start_docker_sandbox(settings, session_id, Path(cwd))

    return RuntimeBundle(
        api_client=resolved_api_client,
        cwd=cwd,
        mcp_manager=mcp_manager,
        tool_registry=tool_registry,
        app_state=app_state,
        hook_executor=hook_executor,
        engine=engine,
        commands=create_default_command_registry(
            plugin_commands=[
                command
                for plugin in plugins
                if plugin.enabled
                for command in plugin.commands
            ]
        ),
        external_api_client=api_client is not None,
        enforce_max_turns=enforce_max_turns or max_turns is not None,
        session_id=session_id,
        settings_overrides=settings_overrides,
        session_backend=session_backend or DEFAULT_SESSION_BACKEND,
        extra_skill_dirs=normalized_skill_dirs,
        extra_plugin_roots=normalized_plugin_roots,
    )


async def start_runtime(bundle: RuntimeBundle) -> None:
    """Run session start hooks."""
    await bundle.hook_executor.execute(
        HookEvent.SESSION_START,
        {"cwd": bundle.cwd, "event": HookEvent.SESSION_START.value},
    )


async def close_runtime(bundle: RuntimeBundle) -> None:
    """Close runtime-owned resources."""
    from openharness.sandbox.session import stop_docker_sandbox

    try:
        await _shutdown_async_agents(bundle.engine.tool_metadata)
    except Exception:
        pass

    await stop_docker_sandbox()
    # Extract local environment rules from session before closing
    try:
        from openharness.personalization.session_hook import update_rules_from_session
        update_rules_from_session(bundle.engine.messages)
    except Exception:
        pass  # personalization is best-effort, never block session end

    await bundle.mcp_manager.close()
    await bundle.hook_executor.execute(
        HookEvent.SESSION_END,
        {"cwd": bundle.cwd, "event": HookEvent.SESSION_END.value},
    )


def _async_agent_task_entries(tool_metadata: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(tool_metadata, dict):
        return []
    value = tool_metadata.get("async_agent_tasks")
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def _build_terminal_notice(entry: dict[str, object]) -> str:
    expert_or_agent_id = str(entry.get("expert_id") or entry.get("agent_id") or entry.get("task_id") or "").strip() or "unknown"
    status = str(entry.get("status") or "unknown").strip() or "unknown"
    return_code = entry.get("return_code")
    details = [f"{expert_or_agent_id} finished with status={status}"]
    if isinstance(return_code, int):
        details.append(f"exit_code={return_code}")
    last_message = str(entry.get("last_message") or "").strip()
    if last_message:
        details.append(f"message={last_message[:200]}")
    return ", ".join(details)


def _refresh_async_agent_queue_state(tool_metadata: dict[str, object] | None) -> None:
    """Refresh async agent statuses once and record terminal update notices."""
    if not isinstance(tool_metadata, dict):
        return

    from openharness.extended.channel import get_channel_registry
    from openharness.tasks.manager import get_task_manager

    terminal_statuses = {"completed", "failed", "killed"}
    manager = get_task_manager()
    channel_registry = get_channel_registry()

    for entry in _async_agent_task_entries(tool_metadata):
        task_id = str(entry.get("task_id") or "").strip()
        if not task_id:
            continue
        source_tool = str(entry.get("source_tool") or "").strip()
        terminal_update = False

        if source_tool == "delegate_to_expert":
            expert_id = str(entry.get("expert_id") or entry.get("agent_id") or task_id).strip()
            handle = channel_registry.get_by_expert_id(expert_id)
            if handle is None:
                entry["status"] = "missing"
                entry["is_running"] = False
                continue

            channel_registry.refresh(handle)
            queue_status = str(handle.status or "").strip().lower()
            entry["last_action"] = handle.last_action
            entry["last_message"] = handle.last_message
            entry["last_screenshot"] = handle.last_screenshot

            if queue_status in terminal_statuses:
                entry["status"] = queue_status
                entry["return_code"] = handle.process.exitcode
                entry["is_running"] = False
                terminal_update = True
            elif handle.process.is_alive():
                entry["status"] = "running"
                entry["is_running"] = True
            else:
                code = handle.process.exitcode
                entry["return_code"] = code
                entry["status"] = "completed" if code == 0 else "failed"
                entry["is_running"] = False
                terminal_update = True
        else:
            task = manager.get_task(task_id)
            if task is None:
                entry["status"] = "missing"
                entry["is_running"] = False
                continue
            entry["status"] = task.status
            entry["return_code"] = task.return_code
            entry["is_running"] = task.status not in terminal_statuses
            terminal_update = task.status in terminal_statuses

        if terminal_update:
            entry["has_terminal_update"] = True
            entry["terminal_status"] = entry.get("status")
            if not bool(entry.get("terminal_notice_pending")):
                entry["terminal_notice_pending"] = True
                entry["terminal_notice_text"] = _build_terminal_notice(entry)


def _pending_terminal_notices(tool_metadata: dict[str, object] | None) -> list[str]:
    notices: list[str] = []
    for entry in _async_agent_task_entries(tool_metadata):
        if not bool(entry.get("terminal_notice_pending")):
            continue
        text = str(entry.get("terminal_notice_text") or "").strip()
        if text:
            notices.append(text)
    return notices


def _mark_terminal_notices_delivered(tool_metadata: dict[str, object] | None) -> None:
    for entry in _async_agent_task_entries(tool_metadata):
        if bool(entry.get("terminal_notice_pending")):
            entry["terminal_notice_pending"] = False
            entry["terminal_notice_delivered"] = True


def _merge_prompt_with_terminal_notices(prompt: str, notices: list[str]) -> str:
    if not notices:
        return prompt
    notice_lines = "\n".join(f"- {item}" for item in notices)
    return (
        "Background agent terminal updates:\n"
        f"{notice_lines}\n\n"
        "User request:\n"
        f"{prompt}"
    )


def _terminate_process_handle(process: object) -> None:
    terminate = getattr(process, "terminate", None)
    if callable(terminate):
        terminate()


def _kill_process_handle(process: object) -> None:
    kill = getattr(process, "kill", None)
    if callable(kill):
        kill()


async def _shutdown_async_agents(tool_metadata: dict[str, object] | None) -> None:
    """Ensure no background async agents survive runtime shutdown."""
    if not isinstance(tool_metadata, dict):
        return

    from openharness.extended.channel import get_channel_registry
    from openharness.tasks.manager import get_task_manager

    terminal_statuses = {"completed", "failed", "killed", "missing"}
    manager = get_task_manager()
    channel_registry = get_channel_registry()
    spawn_handles: list[tuple[dict[str, object], object]] = []

    _refresh_async_agent_queue_state(tool_metadata)
    for entry in _async_agent_task_entries(tool_metadata):
        task_id = str(entry.get("task_id") or "").strip()
        if not task_id:
            continue
        if str(entry.get("source_tool") or "").strip() == "delegate_to_expert":
            expert_id = str(entry.get("expert_id") or entry.get("agent_id") or task_id).strip()
            handle = channel_registry.get_by_expert_id(expert_id)
            if handle is None:
                continue
            channel_registry.refresh(handle)
            if handle.process.is_alive():
                try:
                    channel_registry.send_downlink(
                        handle,
                        kind="terminate",
                        payload={"text": "", "message": "terminated by runtime shutdown"},
                    )
                except Exception:
                    pass
                spawn_handles.append((entry, handle))
            continue

        task = manager.get_task(task_id)
        if task is None or task.status in terminal_statuses:
            continue
        try:
            await manager.stop_task(task_id)
            entry["status"] = "killed"
            entry["is_running"] = False
            entry["return_code"] = task.return_code
        except Exception:
            pass

    if not spawn_handles:
        return

    deadline = asyncio.get_running_loop().time() + 3.0
    while asyncio.get_running_loop().time() < deadline:
        alive = False
        for entry, handle in spawn_handles:
            channel_registry.refresh(handle)
            if handle.process.is_alive():
                alive = True
            else:
                entry["status"] = str(handle.status or "killed")
                entry["is_running"] = False
                entry["return_code"] = handle.process.exitcode
        if not alive:
            break
        await asyncio.sleep(0.1)

    for entry, handle in spawn_handles:
        if not handle.process.is_alive():
            continue
        try:
            _terminate_process_handle(handle.process)
        except Exception:
            pass
        await asyncio.sleep(0.05)
        if handle.process.is_alive():
            try:
                _kill_process_handle(handle.process)
            except Exception:
                pass
        entry["status"] = "killed"
        entry["is_running"] = False


def _last_user_text(messages: list[ConversationMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "user" and msg.text.strip():
            return msg.text.strip()
    return ""


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _format_pending_tool_results(messages: list[ConversationMessage]) -> str | None:
    """Render a compact summary when we stop after tool execution but before the follow-up model turn."""
    if not messages:
        return None

    last = messages[-1]
    if last.role != "user":
        return None
    tool_results = [block for block in last.content if isinstance(block, ToolResultBlock)]
    if not tool_results:
        return None

    tool_uses_by_id: dict[str, ToolUseBlock] = {}
    assistant_text = ""
    for msg in reversed(messages[:-1]):
        if msg.role != "assistant":
            continue
        if not msg.tool_uses:
            continue
        assistant_text = msg.text.strip()
        for tu in msg.tool_uses:
            tool_uses_by_id[tu.id] = tu
        break

    lines: list[str] = [
        "Pending continuation: tool results were produced, but the model did not get a chance to respond yet."
    ]
    if assistant_text:
        lines.append(f"Last assistant message: {_truncate(assistant_text, 400)}")

    max_results = 3
    for tr in tool_results[:max_results]:
        tu = tool_uses_by_id.get(tr.tool_use_id)
        if tu is not None:
            raw_input = json.dumps(tu.input, ensure_ascii=True, sort_keys=True)
            lines.append(
                f"- {tu.name} {_truncate(raw_input, 200)} -> {_truncate(tr.content.strip(), 400)}"
            )
        else:
            lines.append(
                f"- tool_result[{tr.tool_use_id}] -> {_truncate(tr.content.strip(), 400)}"
            )

    if len(tool_results) > max_results:
        lines.append(f"(+{len(tool_results) - max_results} more tool results)")

    lines.append("To continue from these results, run: /continue [COUNT].")
    return "\n".join(lines)


def sync_app_state(bundle: RuntimeBundle) -> None:
    """Refresh UI state from current settings and dynamic keybindings."""
    settings = bundle.current_settings()
    if bundle.enforce_max_turns:
        bundle.engine.set_max_turns(settings.max_turns)
    provider = detect_provider(settings)
    bundle.app_state.set(
        model=settings.model,
        permission_mode=settings.permission.mode.value,
        theme=settings.theme,
        cwd=bundle.cwd,
        provider=provider.name,
        auth_status=auth_status(settings),
        base_url=settings.base_url or "",
        vim_enabled=settings.vim_mode,
        voice_enabled=settings.voice_mode,
        voice_available=provider.voice_supported,
        voice_reason=provider.voice_reason,
        fast_mode=settings.fast_mode,
        effort=settings.effort,
        passes=settings.passes,
        mcp_connected=sum(1 for status in bundle.mcp_manager.list_statuses() if status.state == "connected"),
        mcp_failed=sum(1 for status in bundle.mcp_manager.list_statuses() if status.state == "failed"),
        bridge_sessions=len(get_bridge_manager().list_sessions()),
        output_style=settings.output_style,
        keybindings=load_keybindings(),
    )


def refresh_runtime_client(bundle: RuntimeBundle) -> None:
    """Refresh the active runtime client after provider/auth/profile changes."""
    settings = bundle.current_settings()
    if not bundle.external_api_client:
        bundle.api_client = _resolve_api_client_from_settings(settings)
        bundle.engine.set_api_client(bundle.api_client)
        bundle.hook_executor.update_context(
            api_client=bundle.api_client,
            default_model=settings.model,
        )
    bundle.engine.set_model(settings.model)
    sync_app_state(bundle)


async def handle_line(
    bundle: RuntimeBundle,
    line: str,
    *,
    print_system: SystemPrinter,
    render_event: StreamRenderer,
    clear_output: ClearHandler,
) -> bool:
    """Handle one submitted line for either headless or TUI rendering."""
    _refresh_async_agent_queue_state(bundle.engine.tool_metadata)
    terminal_notices = _pending_terminal_notices(bundle.engine.tool_metadata)
    if not bundle.external_api_client:
        bundle.hook_executor.update_registry(
            load_hook_registry(bundle.current_settings(), bundle.current_plugins())
        )

    parsed = bundle.commands.lookup(line)
    if parsed is not None:
        command, args = parsed
        result = await command.handler(
            args,
            CommandContext(
                engine=bundle.engine,
                hooks_summary=bundle.hook_summary(),
                mcp_summary=bundle.mcp_summary(),
                plugin_summary=bundle.plugin_summary(),
                cwd=bundle.cwd,
                tool_registry=bundle.tool_registry,
                app_state=bundle.app_state,
                session_backend=bundle.session_backend,
                session_id=bundle.session_id,
                extra_skill_dirs=bundle.extra_skill_dirs,
                extra_plugin_roots=bundle.extra_plugin_roots,
            ),
        )
        if result.refresh_runtime:
            refresh_runtime_client(bundle)
        await _render_command_result(result, print_system, clear_output, render_event)
        if result.submit_prompt is not None:
            original_model = bundle.engine.model
            if result.submit_model:
                bundle.engine.set_model(result.submit_model)
            settings = bundle.current_settings()
            submit_prompt = _merge_prompt_with_terminal_notices(result.submit_prompt, terminal_notices)
            system_prompt = build_runtime_system_prompt(
                settings,
                cwd=bundle.cwd,
                latest_user_prompt=submit_prompt,
                extra_skill_dirs=bundle.extra_skill_dirs,
                extra_plugin_roots=bundle.extra_plugin_roots,
            )
            bundle.engine.set_system_prompt(system_prompt)
            try:
                if terminal_notices:
                    _mark_terminal_notices_delivered(bundle.engine.tool_metadata)
                async for event in bundle.engine.submit_message(submit_prompt):
                    await render_event(event)
            except MaxTurnsExceeded as exc:
                await print_system(f"Stopped after {exc.max_turns} turns (max_turns).")
                pending = _format_pending_tool_results(bundle.engine.messages)
                if pending:
                    await print_system(pending)
            finally:
                if result.submit_model:
                    bundle.engine.set_model(original_model)
            bundle.session_backend.save_snapshot(
                cwd=bundle.cwd,
                model=bundle.engine.model,
                system_prompt=system_prompt,
                messages=bundle.engine.messages,
                usage=bundle.engine.total_usage,
                session_id=bundle.session_id,
                tool_metadata=bundle.engine.tool_metadata,
            )
        if result.continue_pending:
            settings = bundle.current_settings()
            if bundle.enforce_max_turns:
                bundle.engine.set_max_turns(settings.max_turns)
            system_prompt = build_runtime_system_prompt(
                settings,
                cwd=bundle.cwd,
                latest_user_prompt=_last_user_text(bundle.engine.messages),
                extra_skill_dirs=bundle.extra_skill_dirs,
                extra_plugin_roots=bundle.extra_plugin_roots,
            )
            bundle.engine.set_system_prompt(system_prompt)
            turns = result.continue_turns if result.continue_turns is not None else bundle.engine.max_turns
            try:
                async for event in bundle.engine.continue_pending(max_turns=turns):
                    await render_event(event)
            except MaxTurnsExceeded as exc:
                await print_system(f"Stopped after {exc.max_turns} turns (max_turns).")
                pending = _format_pending_tool_results(bundle.engine.messages)
                if pending:
                    await print_system(pending)
            bundle.session_backend.save_snapshot(
                cwd=bundle.cwd,
                model=settings.model,
                system_prompt=system_prompt,
                messages=bundle.engine.messages,
                usage=bundle.engine.total_usage,
                session_id=bundle.session_id,
                tool_metadata=bundle.engine.tool_metadata,
            )
        sync_app_state(bundle)
        return not result.should_exit

    settings = bundle.current_settings()
    if bundle.enforce_max_turns:
        bundle.engine.set_max_turns(settings.max_turns)
    effective_line = _merge_prompt_with_terminal_notices(line, terminal_notices)
    system_prompt = build_runtime_system_prompt(
        settings,
        cwd=bundle.cwd,
        latest_user_prompt=effective_line,
        extra_skill_dirs=bundle.extra_skill_dirs,
        extra_plugin_roots=bundle.extra_plugin_roots,
    )
    bundle.engine.set_system_prompt(system_prompt)
    try:
        if terminal_notices:
            _mark_terminal_notices_delivered(bundle.engine.tool_metadata)
        async for event in bundle.engine.submit_message(effective_line):
            await render_event(event)
    except MaxTurnsExceeded as exc:
        await print_system(f"Stopped after {exc.max_turns} turns (max_turns).")
        pending = _format_pending_tool_results(bundle.engine.messages)
        if pending:
            await print_system(pending)
        bundle.session_backend.save_snapshot(
            cwd=bundle.cwd,
            model=settings.model,
            system_prompt=system_prompt,
            messages=bundle.engine.messages,
            usage=bundle.engine.total_usage,
            session_id=bundle.session_id,
            tool_metadata=bundle.engine.tool_metadata,
        )
        sync_app_state(bundle)
        return True
    bundle.session_backend.save_snapshot(
        cwd=bundle.cwd,
        model=settings.model,
        system_prompt=system_prompt,
        messages=bundle.engine.messages,
        usage=bundle.engine.total_usage,
        session_id=bundle.session_id,
        tool_metadata=bundle.engine.tool_metadata,
    )
    sync_app_state(bundle)
    return True


async def _render_command_result(
    result: CommandResult,
    print_system: SystemPrinter,
    clear_output: ClearHandler,
    render_event: StreamRenderer | None = None,
) -> None:
    if result.clear_screen:
        await clear_output()
    if result.replay_messages and render_event is not None:
        # Replay restored conversation messages as transcript events
        from openharness.engine.stream_events import AssistantTextDelta, AssistantTurnComplete
        from openharness.api.usage import UsageSnapshot

        await clear_output()
        await print_system("Session restored:")
        for msg in result.replay_messages:
            if msg.role == "user":
                await print_system(f"> {msg.text}")
            elif msg.role == "assistant" and msg.text.strip():
                await render_event(AssistantTextDelta(text=msg.text))
                await render_event(AssistantTurnComplete(message=msg, usage=UsageSnapshot()))
    if result.message and not result.replay_messages:
        await print_system(result.message)
