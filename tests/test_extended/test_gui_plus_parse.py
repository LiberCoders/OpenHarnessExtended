from __future__ import annotations

import pytest

from openharness.extended.experts.mobile_gui.backends.gui_plus_backend import GuiPlusBackend
from openharness.extended.experts.mobile_gui.types import ActionType, GuiAction, InferResult, Observation


def _backend() -> GuiPlusBackend:
    return GuiPlusBackend(
        {
            "model": "gui-plus-test",
            "base_url": "http://localhost",
            "api_key": "x",
        }
    )


def _ir(text: str) -> InferResult:
    """GUI-Plus encodes its action in the response text, so wrap it as InferResult."""
    return InferResult(text=text, model_request=None)


def test_parse_swipe_accepts_coordinate1_and_coordinate2() -> None:
    b = _backend()
    text = """Action: swipe
<tool_call>
{"name": "mobile_use", "arguments": {"action": "swipe", "coordinate1": [100, 200], "coordinate2": [300, 400]}}
</tool_call>"""
    actions = b.parse_action(_ir(text))
    assert len(actions) == 1
    act = actions[0]
    assert act.action == ActionType.SWIPE
    assert act.x == 100 and act.y == 200 and act.x2 == 300 and act.y2 == 400


def test_parse_strips_thinking_before_tool_call() -> None:
    b = _backend()
    text = """<thinking>
reasoning here
</thinking>
Action: tap
<tool_call>
{"name": "mobile_use", "arguments": {"action": "click", "coordinate": [500, 500]}}
</tool_call>"""
    actions = b.parse_action(_ir(text))
    assert len(actions) == 1
    act = actions[0]
    assert act.action == ActionType.CLICK and act.x == 500 and act.y == 500


def test_parse_home_back_actions() -> None:
    b = _backend()
    home = """Action: home
<tool_call>
{"name":"mobile_use","arguments":{"action":"home"}}
</tool_call>"""
    back = """Action: back
<tool_call>
{"name":"mobile_use","arguments":{"action":"back"}}
</tool_call>"""
    assert b.parse_action(_ir(home))[0].action == ActionType.HOME
    assert b.parse_action(_ir(back))[0].action == ActionType.BACK


def test_adapt_swipe_maps_both_endpoints() -> None:
    pytest.importorskip("PIL", reason="Pillow needed for JPEG fixture")
    import tempfile
    from io import BytesIO
    from pathlib import Path

    from PIL import Image

    b = _backend()
    buf = BytesIO()
    Image.new("RGB", (800, 600), color=(128, 128, 128)).save(buf, format="JPEG", quality=85)
    raw = buf.getvalue()
    with tempfile.NamedTemporaryFile(suffix=".jpeg", delete=False) as f:
        f.write(raw)
        path = f.name
    try:
        act = GuiAction(action=ActionType.SWIPE, x=1000, y=1000, x2=0, y2=0, seconds=0.5)
        adapted_list = b.adapt_action(actions=[act], observation=Observation(screenshot_path=path))
        assert len(adapted_list) == 1
        adapted = adapted_list[0]
        assert adapted.x is not None and adapted.y is not None
        assert adapted.x2 is not None and adapted.y2 is not None
        assert adapted.x >= adapted.x2
    finally:
        Path(path).unlink(missing_ok=True)
