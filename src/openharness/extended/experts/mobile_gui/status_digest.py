"""Build lightweight status digests for leader queries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openharness.extended.experts.mobile_gui.context import MobileGuiContext

# Soft cap for free-text fields forwarded to the leader. The full, untruncated
# payload always remains on disk in step.json; this only bounds what we push
# into the main conversation's context.
_TEXT_FIELD_LIMIT = 16000


def build_status_digest(context: MobileGuiContext, *, status: str, base_dir: object = None) -> dict[str, str]:
    """Return the normalized status payload published over the channel."""
    return {
        "status": status,
        "step": str(context.step),
        "last_action": context.last_action,
        "message": context.last_message,
        "last_screenshot": _readable_path(context.last_screenshot, base_dir),
    }


def _readable_path(path: object, base_dir: object) -> str:
    """Return a path the leader's Read tool can open directly.

    The leader resolves relative paths against its working directory (cwd), which
    is also the worker's ``config.cwd``. So when an artifact lives under cwd
    (the common case, e.g. ``OPENHARNESS_DATA_DIR`` set under the repo), emit a
    cwd-relative path → ``Read(path)`` just works. When it lives outside cwd
    (e.g. the default ``~/.openharness/data``), emit an absolute path → ``Read``
    still opens it directly. Either way: no manual root-joining needed.
    """
    if not path:
        return ""
    resolved = Path(str(path)).resolve()
    if base_dir:
        try:
            return resolved.relative_to(Path(str(base_dir)).resolve()).as_posix()
        except (ValueError, OSError):
            pass
    return str(resolved)


def _clip_text(value: object, limit: int = _TEXT_FIELD_LIMIT) -> str:
    """Bound a free-text field, marking where the full version still lives."""
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<clipped {len(text) - limit} chars; full text in step.json>"


def _compact_action(d: dict[str, Any]) -> dict[str, Any]:
    """Drop None-valued keys from a serialized action dict for the leader payload."""
    return {k: v for k, v in d.items() if v is not None}


def _compact_action_result(d: dict[str, Any]) -> dict[str, Any]:
    """Trim an action_result dict for the leader: drop action (redundant with
    adapted_actions by index), null output_path, and empty stderr in commands."""
    commands = []
    for cmd in d.get("commands", []):
        entry = {k: v for k, v in cmd.items() if not (k == "stderr" and not v)}
        commands.append(entry)
    result: dict[str, Any] = {
        "message": d.get("message", ""),
        "ok": d.get("ok"),
    }
    if d.get("output_path") is not None:
        result["output_path"] = d["output_path"]
    result["commands"] = commands
    return result


def build_step_event(
    step_payload: dict[str, Any],
    *,
    state_root: object,
    base_dir: object,
) -> dict[str, Any]:
    """Leader-facing per-step trajectory view.

    Whitelist of text + image *paths* only. Deliberately omits ``model_request``
    (which carries the system prompt and base64 screenshot) so the leader never
    ingests base64 or prompt scaffolding — images are surfaced as paths.

    Paths are made directly readable by the leader (cwd-relative when under
    ``base_dir`` = the working directory, else absolute) via ``_readable_path``.

    Fields omitted vs the on-disk step.json:
    - status: mid-run steps are implicitly running; terminal status lives in result
    - last_action/message (step level): redundant with action_results entries
    - action_results[].action: redundant with adapted_actions by index
    - null/empty noise fields (output_path, stderr, action null coords)
    - reasoning_content/raw_response when empty
    """
    step = step_payload.get("step")
    step_json_abs = ""
    if isinstance(step, int) and step > 0 and state_root:
        step_json_abs = str(Path(str(state_root)) / "steps" / f"step_{step:04d}" / "step.json")

    event: dict[str, Any] = {
        "step": step,
        "screenshot_path": _readable_path(step_payload.get("screenshot_path", ""), base_dir),
        "step_json_path": _readable_path(step_json_abs, base_dir),
        "parsed_actions": [_compact_action(a) for a in (step_payload.get("parsed_actions") or [])],
        "adapted_actions": [_compact_action(a) for a in (step_payload.get("adapted_actions") or [])],
        "action_results": [_compact_action_result(r) for r in (step_payload.get("action_results") or [])],
    }
    reasoning = _clip_text(step_payload.get("reasoning_content", ""))
    if reasoning:
        event["reasoning_content"] = reasoning
    raw = _clip_text(step_payload.get("raw_response", ""))
    if raw:
        event["raw_response"] = raw
    return event

