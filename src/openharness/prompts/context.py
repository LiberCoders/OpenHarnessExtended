"""Higher-level system prompt assembly."""

from __future__ import annotations

from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Iterable

from openharness.config.paths import (
    get_project_active_repo_context_path,
    get_project_issue_file,
    get_project_pr_comments_file,
)
from openharness.config.settings import Settings
from openharness.coordinator.coordinator_mode import get_coordinator_system_prompt, is_coordinator_mode
from openharness.memory import load_memory_prompt
from openharness.memory.relevance import format_relevant_memories, select_relevant_memories
from openharness.memory.usage import mark_memory_used
from openharness.personalization.rules import load_local_rules
from openharness.permissions.modes import PermissionMode
from openharness.prompts.claudemd import load_claude_md_prompt
from openharness.prompts.environment import get_environment_info
from openharness.prompts.system_prompt import build_system_prompt
from openharness.skills.loader import load_skill_registry


def _pf(settings: Settings, name: str) -> bool:
    """Return whether a named system-prompt section is enabled.

    Defaults to ``True`` when the flag is absent so partial or missing
    ``prompt_fields`` config preserves the historical (all-sections) behaviour.
    """
    return bool(getattr(settings.prompt_fields, name, True))


def _build_mobile_apps_section(settings: Settings) -> str | None:
    """Build a section listing mobile phone apps from ``prompt_meta['apps']``."""
    apps = (settings.prompt_meta or {}).get("apps")
    if not isinstance(apps, list) or not apps:
        return None
    lines = [
        "# Mobile Phone Apps",
        "",
        "The following apps are available on the connected mobile phone. "
        "When operating the phone via the `mobile_gui` expert, refer to them by name:",
        "",
    ]
    header_len = len(lines)
    for app in apps:
        if not isinstance(app, dict):
            continue
        name = str(app.get("name") or "").strip()
        if not name:
            continue
        package = str(app.get("package") or "").strip()
        role = str(app.get("role") or app.get("description") or "").strip()
        line = f"- **{name}**" + (f" (`{package}`)" if package else "")
        if role:
            line += f": {role}"
        lines.append(line)
    if len(lines) == header_len:
        return None
    return "\n".join(lines)


def _build_skills_section(
    cwd: str | Path,
    *,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    settings: Settings | None = None,
) -> str | None:
    """Build a system prompt section listing available skills."""
    registry = load_skill_registry(
        cwd,
        extra_skill_dirs=extra_skill_dirs,
        extra_plugin_roots=extra_plugin_roots,
        settings=settings,
    )
    skills = [skill for skill in registry.list_skills() if not skill.disable_model_invocation]
    if not skills:
        return None
    lines = [
        "# Available Skills",
        "",
        "The following skills are available via the `skill` tool. "
        "When a user's request matches a skill, invoke it with `skill(name=\"<skill_name>\")` "
        "to load detailed instructions before proceeding. "
        "User-invocable skills can also be run directly by the user as `/<skill-name>`.",
        "",
    ]
    for skill in skills:
        command_name = skill.command_name or skill.name
        display = f" ({skill.display_name})" if skill.display_name else ""
        lines.append(f"- **{command_name}**{display}: {skill.description}")
    return "\n".join(lines)


def _build_delegation_section() -> str:
    """Build a concise section describing delegation and worker usage."""
    return "\n".join(
        [
            "# Delegation And Subagents",
            "",
            "OpenHarness can delegate background work with the `agent` tool.",
            "Use it when the user explicitly asks for a subagent, background worker, or parallel investigation, "
            "or when the task clearly benefits from splitting off a focused worker.",
            "",
            "Default pattern:",
            '- Spawn with `agent(description=..., prompt=..., subagent_type=\"worker\")`.',
            "- Inspect running or recorded workers with `/agents`.",
            "- Inspect one worker in detail with `/agents show TASK_ID`.",
            "- Send follow-up instructions with `send_message(task_id=..., message=...)`.",
            "- Read worker output with `task_output(task_id=...)`.",
            "",
            "OpenHarness can also dispatch expert workers, running in background, with the `delegate_to_expert` tool "
            "to resolve (sub-)tasks requiring particular expertise or extra execution capabilities.",
            "Typical use cases:",
            '- Operating an external device/app, e.g. `expert_type=\"mobile_gui\"` for mobile phone.',
            "- Operating a software/app without CLI interfaces, where GUI interactions are required.",
            "",
            "Default pattern:",
            "- Spawn an expert worker with `delegate_to_expert(expert_type=..., task=...)`.",
            "- Get expert status, including detailed progress and outputs, with `get_expert_status(expert_id=...)`.",
            "- During the execution, if there are any follow-up instructions, send them with `send_to_expert(expert_id=..., message_type=..., text=...)`.",
            "",
            "Prefer a normal direct answer for simple tasks. Use subagents only when they materially help.",
        ]
    )


def _build_permission_mode_section(settings: Settings) -> str:
    """Build current permission-mode guidance for the model."""
    mode = settings.permission.mode
    if mode == PermissionMode.PLAN:
        guidance = (
            "Plan mode is enabled. Treat this session as read-only planning and analysis. "
            "Do not call mutating tools such as file writes, edits, package installs, "
            "state-changing shell commands, or task-spawning actions unless the user exits plan mode."
        )
    elif mode == PermissionMode.FULL_AUTO:
        guidance = (
            "Full-auto permission mode is enabled. You may use mutating tools when they are necessary "
            "for the user's request, while still keeping changes scoped and intentional."
        )
    else:
        guidance = (
            "Default permission mode is enabled. Read-only tools can run directly; mutating tools "
            "may require explicit user approval."
        )
    return f"# Current Permission Mode\n{guidance}"


def build_runtime_system_prompt(
    settings: Settings,
    *,
    cwd: str | Path,
    latest_user_prompt: str | None = None,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    include_project_memory: bool = True,
) -> str:
    """Build the runtime system prompt with project instructions and memory."""
    if is_coordinator_mode():
        sections = [get_coordinator_system_prompt()]
    else:
        env = get_environment_info(cwd=str(cwd))
        today = (settings.prompt_meta or {}).get("today")
        if today and str(today).strip():
            env = dataclass_replace(env, date=str(today).strip())
        include_env = _pf(settings, "environment")
        custom = settings.system_prompt
        sections = [
            build_system_prompt(
                custom_prompt=custom, env=env, include_environment=include_env
            )
        ]

    sections.append(_build_permission_mode_section(settings))

    if settings.fast_mode and _pf(settings, "session_mode"):
        sections.append(
            "# Session Mode\nFast mode is enabled. Prefer concise replies, minimal tool use, and quicker progress over exhaustive exploration."
        )

    if _pf(settings, "reasoning_settings"):
        sections.append(
            "# Reasoning Settings\n"
            f"- Effort: {settings.effort}\n"
            f"- Passes: {settings.passes}\n"
            "Adjust depth and iteration count to match these settings while still completing the task."
        )

    skills_section = _build_skills_section(
        cwd,
        extra_skill_dirs=extra_skill_dirs,
        extra_plugin_roots=extra_plugin_roots,
        settings=settings,
    )
    if skills_section and not is_coordinator_mode() and _pf(settings, "available_skills"):
        sections.append(skills_section)

    if not is_coordinator_mode() and _pf(settings, "delegation_and_subagents"):
        sections.append(_build_delegation_section())

    mobile_apps_section = _build_mobile_apps_section(settings)
    if mobile_apps_section and _pf(settings, "mobile_phone_apps"):
        sections.append(mobile_apps_section)

    claude_md = load_claude_md_prompt(cwd)
    if claude_md and _pf(settings, "project_instructions"):
        sections.append(claude_md)

    local_rules = load_local_rules()
    if local_rules and _pf(settings, "local_environment_rules"):
        sections.append(f"# Local Environment Rules\n\n{local_rules}")

    for title, path, field_name in (
        ("Issue Context", get_project_issue_file(cwd), "issue_context"),
        ("Pull Request Comments", get_project_pr_comments_file(cwd), "pull_request_comments"),
        ("Active Repo Context", get_project_active_repo_context_path(cwd), "active_repo_context"),
    ):
        if path.exists() and _pf(settings, field_name):
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                sections.append(f"# {title}\n\n```md\n{content[:12000]}\n```")

    if include_project_memory and settings.memory.enabled:
        memory_section = load_memory_prompt(
            cwd,
            max_entrypoint_lines=settings.memory.max_entrypoint_lines,
            max_entrypoint_bytes=settings.memory.max_entrypoint_bytes,
        )
        if memory_section and _pf(settings, "project_memory"):
            sections.append(memory_section)

        if latest_user_prompt and _pf(settings, "relevant_memories"):
            relevant = select_relevant_memories(
                latest_user_prompt,
                cwd,
                max_results=settings.memory.max_files,
            )
            if relevant:
                try:
                    headers = [item.header for item in relevant]
                    mark_memory_used(cwd, headers, memory_dir=headers[0].path.parent)
                except OSError:
                    pass
                sections.append(format_relevant_memories(relevant))

    return "\n\n".join(section for section in sections if section.strip())
