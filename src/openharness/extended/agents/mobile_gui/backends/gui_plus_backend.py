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
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

from openharness.extended.agents.mobile_gui.backends.base import GuiInferenceBackend
from openharness.extended.agents.mobile_gui.context import MobileGuiContext
from openharness.extended.agents.mobile_gui.types import GuiAction, Observation

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
    ) -> tuple[str, dict[str, Any] | None]:
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
        text = str(completion.choices[0].message.content or "")
        self._history.append(
            {
                "output": text,
                "image_path": observation.screenshot_path,
            }
        )
        return text, request_payload

    def parse_action(self, response_text: str) -> tuple[GuiAction, dict[str, Any] | None]:
        """Parse GUI-Plus response using official <tool_call> extraction flow."""
        payload = _extract_gui_plus_arguments(response_text)
        if payload is None:
            return GuiAction(action="wait", seconds=1.0, message="parse_failed"), None
        action = str(payload.get("action") or "").strip().lower()
        if action == "click":
            coordinate = payload.get("coordinate")
            if isinstance(coordinate, list) and len(coordinate) >= 2:
                x, y = coordinate[0], coordinate[1]
                if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                    return GuiAction(action="click", x=int(round(x)), y=int(round(y))), payload
            return GuiAction(action="wait", seconds=1.0, message="invalid_click_payload"), payload
        if action == "wait":
            seconds = payload.get("seconds")
            if seconds is None:
                seconds = payload.get("time")
            if isinstance(seconds, (int, float)):
                return GuiAction(action="wait", seconds=float(seconds)), payload
            return GuiAction(action="wait", seconds=1.0), payload
        if action == "answer":
            message = str(payload.get("text") or payload.get("message") or "")
            return GuiAction(action="terminate", status="success", message=message), payload
        if action == "terminate":
            status = str(payload.get("status") or "success")
            message = str(payload.get("message") or payload.get("text") or "")
            return GuiAction(action="terminate", status=status, message=message), payload
        return GuiAction(action="wait", seconds=1.0, message=f"unsupported_action:{action}"), payload

    def adapt_action(self, *, action: GuiAction, observation: Observation) -> GuiAction:
        if action.action != "click" or action.x is None or action.y is None:
            return action
        mapped_x, mapped_y = _map_mobile_use_coordinate(
            x=action.x,
            y=action.y,
            screenshot_path=observation.screenshot_path,
        )
        return GuiAction(
            action=action.action,
            x=mapped_x,
            y=mapped_y,
            seconds=action.seconds,
            status=action.status,
            message=action.message,
        )

    def _build_messages(self, *, current_image_path: str, instruction: str) -> list[dict[str, Any]]:
        history_n = self._history_n
        current_step = len(self._history)
        history_start_idx = max(0, current_step - history_n)

        previous_actions: list[str] = []
        for idx in range(history_start_idx):
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


def _extract_gui_plus_arguments(response_text: str) -> dict[str, Any] | None:
    # Official flow: extract <tool_call> JSON blocks first.
    pattern = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
    blocks = pattern.findall(response_text or "")
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
    text = str(response_text or "")
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

