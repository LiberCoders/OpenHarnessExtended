"""GUI-Plus backend aligned with local test_gui_api.py protocol.

Reference:
- https://help.aliyun.com/zh/model-studio/gui-automation
"""

from __future__ import annotations

import base64
import json
import math
import mimetypes
import re
import struct
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

from openharness.extended.experts.mobile_gui.backends.base import GuiInferenceBackend
from openharness.extended.experts.mobile_gui.context import MobileGuiContext
from openharness.extended.experts.mobile_gui.types import GuiAction, InferResult, Observation

_MOBILE_TOOLS_JSON = r"""{"type": "function", "function": {"name_for_human": "mobile_use", "name": "mobile_use", "description": "Use a touchscreen to interact with a mobile device, and take screenshots.\n* This is an interface to a mobile device with touchscreen. You can perform actions like clicking, typing, swiping, etc.\n* Some applications may take time to start or process actions, so you may need to wait and take successive screenshots to see the results of your actions.\n* The screen's resolution is 1000x1000.\n* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. Don't click boxes on their edges unless asked.", "parameters": {"properties": {"action": {"description": "The action to perform. The available actions are:\n* `key`: Perform a key event on the mobile device.\n* `click`: Click the point on the screen with coordinate (x, y).\n* `long_press`: Press the point on the screen with coordinate (x, y) for specified seconds.\n* `swipe`: Swipe from the starting point with coordinate (x, y) to the end point with coordinates2 (x2, y2).\n* `type`: Input the specified text into the activated input box.\n* `system_button`: Press the system button.\n* `open`: Open an app on the device.\n* `wait`: Wait specified seconds for the change to happen.\n* `answer`: Terminate the current task and output the answer.\n* `interact`: Resolve the blocking window by interacting with the user.\n* `terminate`: Terminate the current task and report its completion status.", "enum": ["key", "click", "long_press", "swipe", "type", "system_button", "open", "wait", "answer", "interact", "terminate"], "type": "string"}, "coordinate": {"description": "(x, y) coordinates.", "type": "array"}, "coordinate2": {"description": "(x, y) coordinates for swipe end.", "type": "array"}, "text": {"description": "Text payload for actions requiring text.", "type": "string"}, "time": {"description": "Seconds for long_press/wait.", "type": "number"}, "button": {"description": "System button.", "enum": ["Back", "Home", "Menu", "Enter"], "type": "string"}, "status": {"description": "Status for terminate.", "type": "string", "enum": ["success", "failure"]}}, "required": ["action"], "type": "object"}, "args_format": "Format the arguments as a JSON object."}}"""

MOBILE_SYSTEM_PROMPT = f"""# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{_MOBILE_TOOLS_JSON}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{{"name": <function-name>, "arguments": <args-json-object>}}
</tool_call>

# Response format

Response format for every step:
1) Action: a short imperative describing what to do in the UI.
2) A single <tool_call>...</tool_call> block containing only the JSON: {{"name": <function-name>, "arguments": <args-json-object>}}.

Rules:
- Output exactly in the order: Action, <tool_call>.
- Be brief: one line for Action.
- Do not output anything else outside those two parts.
- If finishing, use action=terminate in the tool call."""


class GuiPlusBackend(GuiInferenceBackend):
    """Backend that follows DashScope GUI-Plus prompt/header conventions."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._model = str(config.get("model") or "gui-plus-2026-02-26")
        self._base_url = str(config.get("base_url") or "").rstrip("/")
        self._api_key = str(config.get("api_key") or "")
        self._tls_verify = bool(config.get("tls_verify", True))
        self._max_tokens = int(config.get("max_tokens") or 2048)
        self._history_n = int(config.get("history_n") or 4)
        self._session_id = str(config.get("session_id") or uuid.uuid4())
        self._instruction = str(config.get("instruction") or "").strip()
        self._history: list[dict[str, str]] = []  # [{"output": "...", "image_path": "..."}]
        self._http_client = httpx.Client(verify=self._tls_verify, timeout=120.0)
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            http_client=self._http_client,
            default_headers={"x-dashscope-gui-session-id": self._session_id},
        )

    async def infer(
        self, *, context: MobileGuiContext, observation: Observation
    ) -> InferResult:
        current_instruction = self._instruction or context.task
        if context.extra_instruction.strip():
            current_instruction = f"{current_instruction}\n补充要求：{context.extra_instruction.strip()}"
        messages = self._build_messages(
            current_image_path=observation.screenshot_path,
            instruction=current_instruction,
        )
        request_payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "session_id": self._session_id,
            "messages": messages,
        }
        completion = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=self._max_tokens,
        )
        message = completion.choices[0].message
        text = str(message.content or "")
        reasoning_content = str(getattr(message, "reasoning_content", "") or "")
        self._history.append(
            {
                "output": text,
                "image_path": observation.screenshot_path,
            }
        )
        return InferResult(
            raw_response=text,
            model_request=request_payload,
            reasoning_content=reasoning_content,
        )

    def parse_action(self, response_text: str) -> GuiAction:
        """Parse GUI-Plus response using a parser registry."""
        payload = _extract_gui_plus_arguments(response_text)
        if payload is None:
            raise ValueError("Failed to parse GUI-Plus tool_call arguments")
        action = str(payload.get("action") or "").strip().lower()
        parser = _ACTION_PARSERS.get(action)
        if parser is None and action in {"call_user", "calluser"}:
            action = "interact"
            parser = _ACTION_PARSERS.get(action)
        if parser is not None:
            return parser(payload)
        raise ValueError(f"Unsupported action: {action}")

    def adapt_action(self, *, action: GuiAction, observation: Observation) -> GuiAction:
        fields = _ACTION_COORD_FIELDS.get(action.action)
        if not fields:
            return action
        updates: dict[str, int] = {}
        for fx, fy in fields:
            x = getattr(action, fx)
            y = getattr(action, fy)
            if x is None or y is None:
                continue
            mx, my = _map_mobile_use_coordinate(x=x, y=y, screenshot_path=observation.screenshot_path)
            updates[fx] = mx
            updates[fy] = my
        return replace(action, **updates) if updates else action

    def _build_messages(self, *, current_image_path: str, instruction: str) -> list[dict[str, Any]]:
        history_n = self._history_n
        current_step = len(self._history)
        history_start_idx = max(0, current_step - history_n)

        # NOTE:
        # The official snippet iterates `range(history_start_idx)`, which can produce
        # unintuitive traces (for example, at step 5 with history_n=4 it only keeps step 1).
        #
        # We intentionally keep the *latest* up-to-N previous actions:
        # - current_step <= history_n: include all previous steps;
        # - current_step > history_n: include only the most recent history_n steps.
        #
        # This keeps textual "Previous actions" aligned with `history_tail` images
        # below, so the model receives a consistent short-term trajectory.
        previous_actions: list[str] = []
        for idx in range(history_start_idx, current_step):
            history_output_str = self._history[idx]["output"]
            if "Action:" in history_output_str and "<tool_call>" in history_output_str:
                history_output_str = history_output_str.split("Action:", 1)[1].split("<tool_call>", 1)[0].strip()
            previous_actions.append(f"Step {idx + 1}: {history_output_str}")

        previous_actions_str = "\n".join(previous_actions) if previous_actions else "None"

        today = datetime.today()
        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekday_names[today.weekday()]
        formatted_date = today.strftime("%Y年%m月%d日") + " " + weekday
        ground_info = f"今天的日期是:{formatted_date}。"

        instruction_prompt = (
            "Please generate the next move according to the UI screenshot, instruction and previous actions.\n\n"
            f"Instruction: {ground_info}{instruction}\n\n"
            "Previous actions:\n"
            f"{previous_actions_str}"
        )

        messages: list[dict[str, Any]] = [{"role": "system", "content": MOBILE_SYSTEM_PROMPT}]
        history_tail = self._history[-history_n:] if history_n > 0 else []
        if history_tail:
            for history_id, history_item in enumerate(history_tail):
                image_url = _image_to_data_url(Path(history_item["image_path"]))
                if history_id == 0:
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": instruction_prompt},
                                {"type": "image_url", "image_url": {"url": image_url}},
                            ],
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "user",
                            "content": [{"type": "image_url", "image_url": {"url": image_url}}],
                        }
                    )
                messages.append({"role": "assistant", "content": history_item["output"]})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": _image_to_data_url(Path(current_image_path))}}
                    ],
                }
            )
        else:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction_prompt},
                        {"type": "image_url", "image_url": {"url": _image_to_data_url(Path(current_image_path))}},
                    ],
                }
            )

        return messages

    def close(self) -> None:
        try:
            self._http_client.close()
        except Exception:
            pass
        try:
            self._client.close()
        except Exception:
            pass


def _image_to_data_url(image_path: Path) -> str:
    raw = image_path.read_bytes()
    mime, _ = mimetypes.guess_type(image_path.name)
    if not mime:
        mime = "image/jpeg"
    b64 = base64.standard_b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


_KEY_NAME_TO_CODE: dict[str, int] = {
    "back": 4,
    "home": 3,
    "enter": 66,
    "menu": 82,
    "search": 84,
    "del": 67,
    "delete": 67,
    "volume_up": 24,
    "volume_down": 25,
    "power": 26,
    "camera": 27,
    "tab": 61,
    "space": 62,
    "dpad_up": 19,
    "dpad_down": 20,
    "dpad_left": 21,
    "dpad_right": 22,
    "dpad_center": 23,
}


def _parse_click(payload: dict[str, Any]) -> GuiAction:
    c = _pick_xy_list(payload, "coordinate", "coordinate1")
    if c is None or not _is_xy(c):
        raise ValueError(f"Invalid click payload: {payload!r}")
    return GuiAction(action="click", x=int(round(c[0])), y=int(round(c[1])))


def _parse_wait(payload: dict[str, Any]) -> GuiAction:
    seconds = payload.get("seconds")
    if seconds is None:
        seconds = payload.get("time")
    return GuiAction(action="wait", seconds=float(seconds) if isinstance(seconds, (int, float)) else 1.0)


def _parse_long_press(payload: dict[str, Any]) -> GuiAction:
    c = _pick_xy_list(payload, "coordinate", "coordinate1")
    if c is None or not _is_xy(c):
        raise ValueError(f"Invalid long_press payload: {payload!r}")
    t = payload.get("time")
    if t is None:
        t = payload.get("seconds")
    sec = float(t) if isinstance(t, (int, float)) else 0.8
    return GuiAction(action="long_press", x=int(round(c[0])), y=int(round(c[1])), seconds=sec)


def _parse_swipe(payload: dict[str, Any]) -> GuiAction:
    c1 = _pick_xy_list(payload, "coordinate", "coordinate1")
    c2 = _pick_xy_list(payload, "coordinate2")
    if c1 is None or c2 is None or not _is_xy(c1) or not _is_xy(c2):
        raise ValueError(f"Invalid swipe payload: {payload!r}")
    t = payload.get("time")
    if t is None:
        t = payload.get("seconds")
    sec = float(t) if isinstance(t, (int, float)) else None
    return GuiAction(
        action="swipe",
        x=int(round(c1[0])),
        y=int(round(c1[1])),
        x2=int(round(c2[0])),
        y2=int(round(c2[1])),
        seconds=sec,
    )


def _parse_type(payload: dict[str, Any]) -> GuiAction:
    return GuiAction(action="type", text=str(payload.get("text") or ""))


def _parse_open(payload: dict[str, Any]) -> GuiAction:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError(f"open requires text: {payload!r}")
    return GuiAction(action="open", text=text)


def _parse_home(_: dict[str, Any]) -> GuiAction:
    return GuiAction(action="home")


def _parse_back(_: dict[str, Any]) -> GuiAction:
    return GuiAction(action="back")


def _parse_system_button(payload: dict[str, Any]) -> GuiAction:
    button = str(payload.get("button") or "").strip()
    if not button:
        raise ValueError(f"system_button requires button: {payload!r}")
    lowered = button.lower()
    if lowered == "home":
        return GuiAction(action="home")
    if lowered == "back":
        return GuiAction(action="back")
    mapping = {"menu": 82, "enter": 66}
    keycode = mapping.get(lowered)
    if keycode is not None:
        return GuiAction(action="key", keycode=keycode)
    raise ValueError(f"Unsupported system_button: {button!r}")


def _parse_key(payload: dict[str, Any]) -> GuiAction:
    code = _parse_keycode(payload)
    if code is None:
        raise ValueError(f"Invalid key payload: {payload!r}")
    if code == 3:
        return GuiAction(action="home")
    if code == 4:
        return GuiAction(action="back")
    return GuiAction(action="key", keycode=code)


def _parse_interact(payload: dict[str, Any]) -> GuiAction:
    text = str(payload.get("text") or payload.get("message") or "").strip()
    return GuiAction(action="interact", text=text)


def _parse_answer(payload: dict[str, Any]) -> GuiAction:
    message = str(payload.get("text") or payload.get("message") or "")
    return GuiAction(action="terminate", status="success", message=message)


def _parse_terminate(payload: dict[str, Any]) -> GuiAction:
    status = str(payload.get("status") or "success")
    message = str(payload.get("message") or payload.get("text") or "")
    return GuiAction(action="terminate", status=status, message=message)


_ACTION_PARSERS: dict[str, Any] = {
    "click": _parse_click,
    "wait": _parse_wait,
    "long_press": _parse_long_press,
    "swipe": _parse_swipe,
    "scroll": _parse_swipe,
    "type": _parse_type,
    "open": _parse_open,
    "home": _parse_home,
    "back": _parse_back,
    "system_button": _parse_system_button,
    "key": _parse_key,
    "interact": _parse_interact,
    "answer": _parse_answer,
    "terminate": _parse_terminate,
}

_ACTION_COORD_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "click": (("x", "y"),),
    "long_press": (("x", "y"),),
    "swipe": (("x", "y"), ("x2", "y2")),
}


def _parse_keycode(payload: dict[str, Any]) -> int | None:
    raw = payload.get("keycode")
    if raw is None:
        raw = payload.get("key")
    if raw is None:
        raw = payload.get("text")
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        if s.isdigit():
            return int(s)
        return _KEY_NAME_TO_CODE.get(s.lower())
    return None


def _pick_xy_list(payload: dict[str, Any], *keys: str) -> list[Any] | None:
    """Return the first list value among ``keys`` that has at least two numeric elements."""
    for key in keys:
        raw = payload.get(key)
        if isinstance(raw, list) and len(raw) >= 2:
            return raw
    return None


def _is_xy(values: list[Any]) -> bool:
    return len(values) >= 2 and isinstance(values[0], (int, float)) and isinstance(values[1], (int, float))


def _extract_gui_plus_arguments(response_text: str) -> dict[str, Any] | None:
    # Official flow: extract <tool_call> JSON blocks first.
    pattern = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
    text_for_parse = str(response_text or "")
    blocks = pattern.findall(text_for_parse)
    for block in blocks:
        blob = str(block).strip()
        try:
            tool_call = json.loads(blob)
        except json.JSONDecodeError:
            continue
        args = tool_call.get("arguments")
        if isinstance(args, dict):
            return args
    # Fallback for occasional direct-JSON responses.
    text = text_for_parse
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                args = data.get("arguments")
                if isinstance(args, dict):
                    return args
                return data
        except json.JSONDecodeError:
            return None
    return None


def _map_mobile_use_coordinate(*, x: int, y: int, screenshot_path: str | None) -> tuple[int, int]:
    """Map GUI-Plus 1000x1000 normalized coordinates to runtime pixel coordinates."""
    if screenshot_path and _looks_like_normalized_coordinate(x, y):
        size = _read_image_size(Path(screenshot_path))
        if size is not None:
            width, height = size
            resized_height, resized_width = _smart_resize(
                height=height,
                width=width,
                factor=16,
                min_pixels=3136,
                max_pixels=1003520 * 200,
            )
            mapped_x = int(x / 1000.0 * resized_width)
            mapped_y = int(y / 1000.0 * resized_height)
            return max(0, mapped_x), max(0, mapped_y)
    return max(0, int(x)), max(0, int(y))


def _looks_like_normalized_coordinate(x: int, y: int) -> bool:
    return 0 <= x <= 1000 and 0 <= y <= 1000


def _read_image_size(path: Path) -> tuple[int, int] | None:
    try:
        raw = path.read_bytes()
    except Exception:
        return None
    try:
        if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
            width = struct.unpack(">I", raw[16:20])[0]
            height = struct.unpack(">I", raw[20:24])[0]
            if width > 0 and height > 0:
                return width, height

        if raw.startswith(b"\xff\xd8"):
            idx = 2
            raw_len = len(raw)
            while idx + 9 < raw_len:
                if raw[idx] != 0xFF:
                    idx += 1
                    continue
                marker = raw[idx + 1]
                idx += 2
                if marker in {0xD8, 0xD9}:
                    continue
                if idx + 1 >= raw_len:
                    break
                segment_len = (raw[idx] << 8) + raw[idx + 1]
                if segment_len < 2 or idx + segment_len > raw_len:
                    break
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    if idx + 7 >= raw_len:
                        break
                    height = (raw[idx + 3] << 8) + raw[idx + 4]
                    width = (raw[idx + 5] << 8) + raw[idx + 6]
                    if width > 0 and height > 0:
                        return width, height
                    break
                idx += segment_len
    except Exception:
        return None
    return None


def _smart_resize(
    *,
    height: int,
    width: int,
    factor: int = 32,
    min_pixels: int = 32 * 32 * 4,
    max_pixels: int = 32 * 32 * 1280,
    max_long_side: int = 8192,
) -> tuple[int, int]:
    """Compute GUI-Plus internal resized shape from the source image shape."""

    def _round_by_factor(number: float, step: int) -> int:
        return int(round(number / step) * step)

    def _ceil_by_factor(number: float, step: int) -> int:
        return int(math.ceil(number / step) * step)

    def _floor_by_factor(number: float, step: int) -> int:
        return int(math.floor(number / step) * step)

    if height < 2 or width < 2:
        raise ValueError("height/width must be >= 2")
    if max(height, width) / min(height, width) > 200:
        raise ValueError("absolute aspect ratio must be <= 200")

    if max(height, width) > max_long_side:
        beta = max(height, width) / max_long_side
        height, width = int(height / beta), int(width / beta)

    resized_h = _round_by_factor(height, factor)
    resized_w = _round_by_factor(width, factor)

    if resized_h * resized_w > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        resized_h = _floor_by_factor(height / beta, factor)
        resized_w = _floor_by_factor(width / beta, factor)
    elif resized_h * resized_w < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        resized_h = _ceil_by_factor(height * beta, factor)
        resized_w = _ceil_by_factor(width * beta, factor)

    return resized_h, resized_w

