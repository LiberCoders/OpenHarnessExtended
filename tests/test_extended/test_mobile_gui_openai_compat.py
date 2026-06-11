from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openharness.extended.experts.mobile_gui.backends.openai_compat_backend import (
    OpenAICompatBackend,
    build_system_prompt,
    build_tools,
)
from openharness.extended.experts.mobile_gui.backends.registry import resolve_gui_backend
from openharness.extended.experts.mobile_gui.context import MobileGuiContext
from openharness.extended.experts.mobile_gui.types import ActionType, InferResult, Observation


def _fake_png(path: Path, width: int, height: int) -> Path:
    """A minimal byte blob whose PNG IHDR carries width/height (enough for read_image_size)."""
    data = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + struct.pack(">I", width)
        + struct.pack(">I", height)
        + b"\x08\x02\x00\x00\x00"
    )
    path.write_bytes(data)
    return path


def _backend(**overrides: Any) -> OpenAICompatBackend:
    cfg: dict[str, Any] = {"model": "m", "api_key": "k", "base_url": "http://example.invalid"}
    cfg.update(overrides)
    return OpenAICompatBackend(cfg)


def _tool_call(name: str, arguments: dict[str, Any], *, call_id: str = "1") -> dict[str, Any]:
    """One tool call in the native OpenAI shape (arguments is a JSON string)."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _ir(name: str, arguments: dict[str, Any], *, call_id: str = "1") -> InferResult:
    """An InferResult carrying one structured tool call, as infer() would return."""
    return InferResult(
        text="",
        model_request=None,
        tool_calls=[_tool_call(name, arguments, call_id=call_id)],
    )


# --------------------------------------------------------------------------- #
# parse_action from structured tool_calls
# --------------------------------------------------------------------------- #


def test_parse_click_from_tool_calls() -> None:
    actions = _backend().parse_action(_ir("click", {"x": 100, "y": 200}))
    assert len(actions) == 1
    a = actions[0]
    assert a.action == ActionType.CLICK and a.x == 100 and a.y == 200


def test_parse_type_and_swipe_and_drag() -> None:
    b = _backend()
    (t,) = b.parse_action(_ir("type", {"text": "hello"}))
    assert t.action == ActionType.TYPE and t.text == "hello"

    (s,) = b.parse_action(_ir("swipe", {"x": 0, "y": 0, "x2": 10, "y2": 20, "time": 0.5}))
    assert s.action == ActionType.SWIPE and (s.x, s.y, s.x2, s.y2) == (0, 0, 10, 20) and s.seconds == 0.5

    (d,) = b.parse_action(_ir("drag", {"x": 1, "y": 2, "x2": 3, "y2": 4}))
    assert d.action == ActionType.DRAG and (d.x, d.y, d.x2, d.y2) == (1, 2, 3, 4)


def test_parse_key_maps_home_back_and_passthrough() -> None:
    b = _backend()
    assert b.parse_action(_ir("key", {"keycode": 3}))[0].action == ActionType.HOME
    assert b.parse_action(_ir("key", {"keycode": 4}))[0].action == ActionType.BACK
    k = b.parse_action(_ir("key", {"keycode": 26}))[0]
    assert k.action == ActionType.KEY and k.keycode == 26


def test_parse_terminate_and_home_back() -> None:
    b = _backend()
    term = b.parse_action(_ir("terminate", {"status": "success", "message": "done"}))[0]
    assert term.action == ActionType.TERMINATE and term.status == "success" and term.text == "done"
    assert b.parse_action(_ir("home", {}))[0].action == ActionType.HOME
    assert b.parse_action(_ir("back", {}))[0].action == ActionType.BACK


def test_parse_no_tool_calls_raises() -> None:
    result = InferResult(text="I will click the button", model_request=None, tool_calls=[])
    with pytest.raises(ValueError, match="no tool_calls"):
        _backend().parse_action(result)


# --------------------------------------------------------------------------- #
# coordinate adaptation
# --------------------------------------------------------------------------- #


def test_adapt_normalized_maps_to_pixels(tmp_path: Path) -> None:
    png = _fake_png(tmp_path / "s.png", width=400, height=800)
    b = _backend(coordinate_space="normalized_1000")
    (click,) = b.parse_action(_ir("click", {"x": 500, "y": 250}))
    obs = Observation(screenshot_path=str(png))
    (adapted,) = b.adapt_action(actions=[click], observation=obs)
    # 500/1000*400 = 200 ; 250/1000*800 = 200
    assert adapted.x == 200 and adapted.y == 200


def test_adapt_pixel_is_passthrough(tmp_path: Path) -> None:
    png = _fake_png(tmp_path / "s.png", width=400, height=800)
    b = _backend(coordinate_space="pixel")
    (click,) = b.parse_action(_ir("click", {"x": 123, "y": 456}))
    obs = Observation(screenshot_path=str(png))
    (adapted,) = b.adapt_action(actions=[click], observation=obs)
    assert adapted.x == 123 and adapted.y == 456


# --------------------------------------------------------------------------- #
# system prompt presets + coordinate clause
# --------------------------------------------------------------------------- #


def test_system_prompt_presets_and_coordinate_clause() -> None:
    norm = build_system_prompt(preset="default", coordinate_space="normalized_1000")
    assert "0-1000" in norm
    pix = build_system_prompt(preset="default", coordinate_space="pixel")
    assert "device pixels" in pix
    concise = build_system_prompt(preset="concise", coordinate_space="pixel")
    detailed = build_system_prompt(preset="detailed", coordinate_space="pixel")
    assert concise.strip() and len(concise) < len(detailed)


def test_system_prompt_explicit_override_is_verbatim() -> None:
    out = build_system_prompt(system_prompt="MY PROMPT", preset="detailed", coordinate_space="normalized_1000")
    assert out == "MY PROMPT"  # no coordinate clause appended


def test_unknown_coordinate_space_rejected() -> None:
    with pytest.raises(ValueError, match="coordinate_space"):
        _backend(coordinate_space="weird")


def test_date_injection_is_off_by_default_and_lives_in_system_prompt() -> None:
    # Off by default: no date grounding leaks into the prompt.
    assert "2026-01-02" not in _backend()._system_prompt
    # On + prompt_meta.today: grounded date appears in the system prompt only.
    b = _backend(inject_date=True, prompt_meta={"today": "2026-01-02"})
    assert "2026-01-02" in b._system_prompt and "Friday" in b._system_prompt


def test_instruction_has_no_task_prefix_or_date(tmp_path: Path) -> None:
    png = _fake_png(tmp_path / "cur.png", width=100, height=200)
    msgs = _backend()._build_messages(current_image_path=str(png), instruction="open settings")
    text = msgs[1]["content"][0]["text"]
    assert text == "open settings"  # bare instruction: no "Task:" prefix, no date


# --------------------------------------------------------------------------- #
# tools schema
# --------------------------------------------------------------------------- #


def test_tools_cover_action_vocabulary() -> None:
    names = {t["function"]["name"] for t in build_tools()}
    assert {"click", "long_press", "swipe", "drag", "type", "open", "key", "home", "back", "wait", "terminate", "interact"} <= names
    # every tool is a standard function tool
    assert all(t["type"] == "function" for t in build_tools())


# --------------------------------------------------------------------------- #
# message construction
# --------------------------------------------------------------------------- #


def test_build_messages_first_step_is_system_then_user_with_image(tmp_path: Path) -> None:
    png = _fake_png(tmp_path / "cur.png", width=100, height=200)
    b = _backend()
    msgs = b._build_messages(current_image_path=str(png), instruction="open settings")
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    kinds = [part["type"] for part in msgs[1]["content"]]
    assert kinds == ["text", "image_url"]
    assert "open settings" in msgs[1]["content"][0]["text"]


def test_build_messages_replays_assistant_and_tool(tmp_path: Path) -> None:
    cur = _fake_png(tmp_path / "cur.png", width=100, height=200)
    hist_img = _fake_png(tmp_path / "h0.png", width=100, height=200)
    b = _backend()
    b._history.append(
        {
            "tool_calls": [_tool_call("click", {"x": 1, "y": 2}, call_id="call_1")],
            "content": "tap it",
            "image_path": str(hist_img),
            "result_texts": ["click(1,2): ok"],
        }
    )
    msgs = b._build_messages(current_image_path=str(cur), instruction="task")
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    assistant = msgs[2]
    assert assistant["tool_calls"][0]["function"]["name"] == "click"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert msgs[3]["tool_call_id"] == "call_1" and "ok" in msgs[3]["content"]


def test_build_messages_replays_all_parallel_tool_calls(tmp_path: Path) -> None:
    cur = _fake_png(tmp_path / "cur.png", width=100, height=200)
    hist_img = _fake_png(tmp_path / "h0.png", width=100, height=200)
    b = _backend()
    b._history.append(
        {
            "tool_calls": [
                _tool_call("click", {"x": 1, "y": 2}, call_id="a"),
                _tool_call("type", {"text": "hi"}, call_id="b"),
            ],
            "content": "two at once",
            "image_path": str(hist_img),
            "result_texts": ["click ok", "type ok"],
        }
    )
    msgs = b._build_messages(current_image_path=str(cur), instruction="task")
    # one assistant message carrying both calls, then one tool reply per call id,
    # each carrying its OWN result (not a single shared string).
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "tool", "user"]
    assert len(msgs[2]["tool_calls"]) == 2
    assert [msgs[3]["tool_call_id"], msgs[4]["tool_call_id"]] == ["a", "b"]
    assert [msgs[3]["content"], msgs[4]["content"]] == ["click ok", "type ok"]


def test_replay_marks_tool_calls_with_no_recorded_result(tmp_path: Path) -> None:
    # Two calls but only one executed (e.g. the first soft-failed): the tail call
    # still gets a role:tool reply, explicitly flagged as not executed.
    cur = _fake_png(tmp_path / "cur.png", width=100, height=200)
    hist_img = _fake_png(tmp_path / "h0.png", width=100, height=200)
    b = _backend()
    b._history.append(
        {
            "tool_calls": [
                _tool_call("click", {"x": 1, "y": 2}, call_id="a"),
                _tool_call("type", {"text": "hi"}, call_id="b"),
            ],
            "content": "",
            "image_path": str(hist_img),
            "result_texts": ["click failed"],
        }
    )
    msgs = b._build_messages(current_image_path=str(cur), instruction="task")
    assert msgs[3]["content"] == "click failed"
    assert msgs[4]["content"] == "NOT EXECUTED"


# --------------------------------------------------------------------------- #
# extra_instruction injection + image window
# --------------------------------------------------------------------------- #


def test_extra_instruction_renders_as_its_own_user_turn(tmp_path: Path) -> None:
    img = _fake_png(tmp_path / "h.png", width=100, height=200)
    cur = _fake_png(tmp_path / "c.png", width=100, height=200)
    b = _backend()
    b._history.append(
        {
            "tool_calls": [_tool_call("click", {"x": 1, "y": 2}, call_id="c1")],
            "content": "",
            "image_path": str(img),
            "result_texts": ["ok"],
            "extra_injected": "focus the search box",
        }
    )
    msgs = b._build_messages(current_image_path=str(cur), instruction="task")
    # The extra appears as a standalone user turn right before that step's
    # screenshot turn, as bare text (no "补充要求：" prefix — the role says it all).
    assert [m["role"] for m in msgs] == ["system", "user", "user", "assistant", "tool", "user"]
    assert msgs[1]["content"] == "focus the search box"


def test_image_window_keeps_recent_screenshots_only(tmp_path: Path) -> None:
    b = _backend(max_images=2)
    for i in range(3):  # 3 history steps + the current one = 4 total
        b._history.append(
            {
                "tool_calls": [_tool_call("click", {}, call_id=f"c{i}")],
                "content": "",
                "image_path": str(_fake_png(tmp_path / f"h{i}.png", width=100, height=200)),
                "result_texts": ["ok"],
                "extra_injected": "",
            }
        )
    cur = _fake_png(tmp_path / "cur.png", width=100, height=200)
    msgs = b._build_messages(current_image_path=str(cur), instruction="task")

    def has_image(m: dict[str, Any]) -> bool:
        return isinstance(m.get("content"), list) and any(
            p.get("type") == "image_url" for p in m["content"]
        )

    def has_omitted(m: dict[str, Any]) -> bool:
        return isinstance(m.get("content"), list) and any(
            p.get("type") == "text" and "omitted" in p.get("text", "") for p in m["content"]
        )

    # max_images=2 -> only the last 2 of 4 steps carry screenshots; the 2 earliest
    # are text-only placeholders...
    assert sum(1 for m in msgs if m["role"] == "user" and has_image(m)) == 2
    assert sum(1 for m in msgs if m["role"] == "user" and has_omitted(m)) == 2
    # ...but text is fully retained — every history step's assistant turn survives.
    assert sum(1 for m in msgs if m["role"] == "assistant") == 3


async def test_infer_injects_only_new_extra_suffix(tmp_path: Path) -> None:
    step_dir = tmp_path / "step_0001"
    step_dir.mkdir()
    png = _fake_png(step_dir / "screenshot.png", width=100, height=200)
    b = _backend()
    b._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: _fake_completion("click", {"x": 1, "y": 2}))
        )
    )
    obs = Observation(screenshot_path=str(png))
    ctx = MobileGuiContext(task="t")

    ctx.extra_instructions.append({"instruction": "A", "timestamp": "2024-01-01T00:00:00+00:00"})
    await b.infer(context=ctx, observation=obs)
    assert b._history[0]["extra_injected"] == "A"

    # Leader appended "B"; only the new entry is injected this step.
    ctx.extra_instructions.append({"instruction": "B", "timestamp": "2024-01-01T00:01:00+00:00"})
    await b.infer(context=ctx, observation=obs)
    assert b._history[1]["extra_injected"] == "B"


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #


def test_registry_resolves_backends_by_explicit_type() -> None:
    from openharness.extended.experts.mobile_gui.backends.gui_plus_backend import GuiPlusBackend

    oc = resolve_gui_backend(gui_backend_config={"type": "openai_compat", "model": "m", "api_key": "k"})
    assert isinstance(oc, OpenAICompatBackend)
    gp = resolve_gui_backend(gui_backend_config={"type": "gui_plus", "model": "m", "api_key": "k"})
    assert isinstance(gp, GuiPlusBackend)


def test_registry_requires_explicit_type() -> None:
    # No default: missing/empty type and an unknown type all error out.
    with pytest.raises(ValueError, match="unsupported gui_backend type"):
        resolve_gui_backend(gui_backend_config={"model": "m"})
    with pytest.raises(ValueError, match="unsupported gui_backend type"):
        resolve_gui_backend(gui_backend_config=None)
    with pytest.raises(ValueError, match="unsupported gui_backend type"):
        resolve_gui_backend(gui_backend_config={"type": "nope"})


# --------------------------------------------------------------------------- #
# infer (mocked client)
# --------------------------------------------------------------------------- #


def _fake_completion(name: str, arguments: dict[str, Any]) -> Any:
    # Real tool-call objects so .model_dump() behaves exactly like the SDK's.
    from openai.types.chat.chat_completion_message_tool_call import (
        ChatCompletionMessageToolCall,
        Function,
    )

    call = ChatCompletionMessageToolCall(
        id="call_42", type="function", function=Function(name=name, arguments=json.dumps(arguments))
    )
    message = SimpleNamespace(content="acting", reasoning_content="", tool_calls=[call])
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")
    return SimpleNamespace(choices=[choice], usage=None, model="m", id="cmpl_1")


async def test_infer_encodes_tool_calls_and_appends_history(tmp_path: Path) -> None:
    step_dir = tmp_path / "step_0001"
    step_dir.mkdir()
    png = _fake_png(step_dir / "screenshot.png", width=100, height=200)

    b = _backend()
    completion = _fake_completion("click", {"x": 10, "y": 20})
    b._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: completion))
    )
    ctx = MobileGuiContext(task="do something")
    obs = Observation(screenshot_path=str(png))

    result = await b.infer(context=ctx, observation=obs)
    # Native OpenAI shape, kept verbatim — arguments stays the JSON string.
    assert result.tool_calls[0]["function"]["name"] == "click"
    assert json.loads(result.tool_calls[0]["function"]["arguments"]) == {"x": 10, "y": 20}
    assert result.text == "acting"  # message.content, kept readable
    assert len(b._history) == 1 and b._history[0]["tool_calls"][0]["id"] == "call_42"

    # the InferResult parses directly — no JSON round-trip
    (action,) = b.parse_action(result)
    assert action.action == ActionType.CLICK and action.x == 10 and action.y == 20
    # dumps were written
    assert (step_dir / "requests.json").is_file()
    assert (step_dir / "response.json").is_file()
