"""Generic OpenAI-compatible GUI backend.

Unlike :class:`GuiPlusBackend` (which is tailored to DashScope's Qwen-GUI
protocol: 1000x1000 + smart_resize coordinates and ``<tool_call>`` text
parsing), this backend speaks the *standard* OpenAI Chat Completions protocol:

- standard multi-turn messages,
- standard function calling via ``tools`` / ``message.tool_calls`` (no regex
  text parsing),
- a configurable **system prompt** (presets or a full override),
- a configurable **coordinate space** (normalized 0-1000 vs. raw device pixels),

so it can target any OpenAI-compatible endpoint and be A/B-tuned by config.

``infer`` returns the structured ``tool_calls`` on the ``InferResult`` and
``parse_action`` reads them directly — no text round-trip. ``raw_response``
holds the readable ``message.content``; the full completion is archived to
``response.json`` by :meth:`_dump_response`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

from openharness.extended.experts.mobile_gui.backends.image import (
    image_to_data_url,
    read_image_size,
    truncate_for_log,
)
from openharness.extended.experts.mobile_gui.backends.base import GuiInferenceBackend
from openharness.extended.experts.mobile_gui.context import MobileGuiContext
from openharness.extended.experts.mobile_gui.types import ActionType, GuiAction, InferResult, Observation

# --------------------------------------------------------------------------- #
# System prompt presets
# --------------------------------------------------------------------------- #

_SYSTEM_PROMPT_PRESETS: dict[str, str] = {
    "default": (
        "You are a mobile GUI agent operating a touchscreen phone.\n"
        "You are given a screenshot of the current screen and a task. At each "
        "step decide the next UI action(s) and call one or more of the provided "
        "tools to perform them; multiple tool calls run in order. Observe the "
        "result in the next screenshot and continue until the task is complete.\n\n"
        "Guidelines:\n"
        "- Call one or more tools per step; they execute in order (e.g. `click` "
        "an input field, then `type`).\n"
        "- Click the center of the target element, not its edges.\n"
        "- If the screen is still loading, call `wait`.\n"
        "- Call `terminate` with status \"success\" when the task is done, or "
        "\"failure\" if it cannot be completed."
    ),
    "concise": (
        "You are a mobile GUI agent. Look at the screenshot and call one or more "
        "tools to perform the next action(s) toward the task; calls run in order. "
        "Call `terminate` when finished."
    ),
    "detailed": (
        "You are an expert mobile GUI automation agent operating a touchscreen "
        "phone.\n\n"
        "At each step you receive the current screenshot (and recent history). "
        "Reason briefly about progress, then call one or more tools for the next "
        "concrete action(s).\n\n"
        "Operating rules:\n"
        "- You may issue several tool calls in one step; they execute in order. "
        "Group naturally sequential actions instead of waiting a full step "
        "between them.\n"
        "- Target the visual center of buttons/icons/fields; avoid edges.\n"
        "- To enter text the field must be focused first — issue `click` then "
        "`type` (sending both in the same step is fine).\n"
        "- After actions that trigger navigation or loading, call `wait` before "
        "assuming the result.\n"
        "- Use `swipe` to scroll; use `drag` only for press-and-move (reordering, "
        "sliders).\n"
        "- Use `open` to launch an app by name or package when that is faster "
        "than navigating.\n"
        "- When the goal is achieved call `terminate` with status \"success\"; if "
        "it is impossible call `terminate` with status \"failure\" and a short "
        "reason.\n"
        "- If a system/permission dialog needs the user, call `interact`."
    ),
}

_DEFAULT_PRESET = "default"

_COORDINATE_SPACES = ("normalized_1000", "pixel")


def _coordinate_clause(coordinate_space: str) -> str:
    """The system-prompt paragraph describing how x/y are interpreted."""
    if coordinate_space == "normalized_1000":
        return (
            "\n\nCoordinate system: all x/y values are integers normalized to the "
            "range 0-1000, where (0,0) is the top-left and (1000,1000) is the "
            "bottom-right corner of the screen."
        )
    return (
        "\n\nCoordinate system: all x/y values are absolute device pixels, where "
        "(0,0) is the top-left corner of the screen."
    )


_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _date_clause(today_override: str | None) -> str:
    """A system-prompt paragraph grounding the model in today's date.

    Uses ``today_override`` (``YYYY-MM-DD``, typically from prompt_meta) when
    given so runs are reproducible; otherwise falls back to the wall clock.
    """
    today = None
    if today_override:
        try:
            today = datetime.strptime(today_override, "%Y-%m-%d")
        except ValueError:
            today = None
    if today is None:
        today = datetime.today()
    return f"\n\nToday's date is {today.strftime('%Y-%m-%d')} ({_WEEKDAY_NAMES[today.weekday()]})."


def build_system_prompt(
    *,
    system_prompt: str = "",
    preset: str = _DEFAULT_PRESET,
    coordinate_space: str = "normalized_1000",
) -> str:
    """Resolve the system prompt.

    An explicit ``system_prompt`` wins and is used verbatim (no coordinate
    clause appended) so the caller has full control. Otherwise the named preset
    body is used and the coordinate clause is appended.
    """
    explicit = (system_prompt or "").strip()
    if explicit:
        return explicit
    body = _SYSTEM_PROMPT_PRESETS.get(preset) or _SYSTEM_PROMPT_PRESETS[_DEFAULT_PRESET]
    return body + _coordinate_clause(coordinate_space)


# --------------------------------------------------------------------------- #
# Tools schema (standard OpenAI function calling)
# --------------------------------------------------------------------------- #


def _coord_param_desc(coordinate_space: str) -> str:
    if coordinate_space == "normalized_1000":
        return "normalized to 0-1000"
    return "absolute device pixels"


def build_tools(coordinate_space: str = "normalized_1000") -> list[dict[str, Any]]:
    """Build the function-tool schema covering the canonical action vocabulary."""
    cdesc = _coord_param_desc(coordinate_space)

    def fn(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    x = {"type": "integer", "description": f"x coordinate ({cdesc})"}
    y = {"type": "integer", "description": f"y coordinate ({cdesc})"}
    x2 = {"type": "integer", "description": f"end x coordinate ({cdesc})"}
    y2 = {"type": "integer", "description": f"end y coordinate ({cdesc})"}
    time_s = {"type": "number", "description": "duration in seconds"}

    return [
        fn("click", "Tap a single point on the screen.", {"x": x, "y": y}, ["x", "y"]),
        fn(
            "long_press",
            "Press and hold a point for `time` seconds.",
            {"x": x, "y": y, "time": time_s},
            ["x", "y"],
        ),
        fn(
            "swipe",
            "Swipe/scroll from (x, y) to (x2, y2).",
            {"x": x, "y": y, "x2": x2, "y2": y2, "time": time_s},
            ["x", "y", "x2", "y2"],
        ),
        fn(
            "drag",
            "Press-and-move from (x, y) to (x2, y2) (reorder, slider).",
            {"x": x, "y": y, "x2": x2, "y2": y2, "time": time_s},
            ["x", "y", "x2", "y2"],
        ),
        fn(
            "type",
            "Type text into the currently focused input field.",
            {"text": {"type": "string", "description": "text to input"}},
            ["text"],
        ),
        fn(
            "open",
            "Open/launch an app by display name or package/bundle name.",
            {
                "name": {
                    "type": "string",
                    "description": 'app display name or package, e.g. "Settings", "Chrome", "com.android.settings"',
                }
            },
            ["name"],
        ),
        fn(
            "key",
            "Send a hardware/system key by Android keycode.",
            {
                "keycode": {
                    "type": "integer",
                    "description": "Android keycode, e.g. 4=BACK, 3=HOME, 66=ENTER, 67=DEL, 82=MENU",
                }
            },
            ["keycode"],
        ),
        fn("home", "Go to the home screen.", {}, []),
        fn("back", "Press the system back button.", {}, []),
        fn("wait", "Wait `time` seconds for the screen to settle.", {"time": time_s}, []),
        fn(
            "terminate",
            "End the task and report completion status.",
            {
                "status": {
                    "type": "string",
                    "enum": ["success", "failure"],
                    "description": "task outcome",
                },
                "message": {"type": "string", "description": "optional final note"},
            },
            ["status"],
        ),
        fn(
            "interact",
            "Hand off to the user to resolve a blocking dialog.",
            {"text": {"type": "string", "description": "what the user should do"}},
            [],
        ),
    ]


# --------------------------------------------------------------------------- #
# Argument coercion + action parsers
# --------------------------------------------------------------------------- #

_KEY_NAME_TO_CODE: dict[str, int] = {
    "back": 4,
    "home": 3,
    "enter": 66,
    "menu": 82,
    "search": 84,
    "del": 67,
    "delete": 67,
    "tab": 61,
    "space": 62,
}


def _to_num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _xy(args: dict[str, Any], xk: str = "x", yk: str = "y", listk: str = "coordinate") -> tuple[int, int] | None:
    """Extract an (x, y) pair, tolerating both scalar keys and a list fallback."""
    x = _to_num(args.get(xk))
    y = _to_num(args.get(yk))
    if x is None or y is None:
        raw = args.get(listk)
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            x = _to_num(raw[0])
            y = _to_num(raw[1])
    if x is None or y is None:
        return None
    return int(round(x)), int(round(y))


def _seconds(args: dict[str, Any]) -> float | None:
    t = args.get("time")
    if t is None:
        t = args.get("seconds")
    n = _to_num(t)
    return n


def _parse_click(args: dict[str, Any]) -> GuiAction:
    xy = _xy(args)
    if xy is None:
        raise ValueError(f"click requires x,y: {args!r}")
    return GuiAction(action=ActionType.CLICK, x=xy[0], y=xy[1])


def _parse_long_press(args: dict[str, Any]) -> GuiAction:
    xy = _xy(args)
    if xy is None:
        raise ValueError(f"long_press requires x,y: {args!r}")
    return GuiAction(action=ActionType.LONG_PRESS, x=xy[0], y=xy[1], seconds=_seconds(args))


def _parse_swipe(args: dict[str, Any]) -> GuiAction:
    a = _xy(args)
    b = _xy(args, "x2", "y2", "coordinate2")
    if a is None or b is None:
        raise ValueError(f"swipe requires x,y,x2,y2: {args!r}")
    return GuiAction(action=ActionType.SWIPE, x=a[0], y=a[1], x2=b[0], y2=b[1], seconds=_seconds(args))


def _parse_drag(args: dict[str, Any]) -> GuiAction:
    a = _xy(args)
    b = _xy(args, "x2", "y2", "coordinate2")
    if a is None or b is None:
        raise ValueError(f"drag requires x,y,x2,y2: {args!r}")
    return GuiAction(action=ActionType.DRAG, x=a[0], y=a[1], x2=b[0], y2=b[1], seconds=_seconds(args))


def _parse_type(args: dict[str, Any]) -> GuiAction:
    return GuiAction(action=ActionType.TYPE, text=str(args.get("text") or ""))


def _parse_open(args: dict[str, Any]) -> GuiAction:
    target = str(args.get("name") or "").strip()
    if not target:
        raise ValueError(f"open requires name: {args!r}")
    return GuiAction(action=ActionType.OPEN, text=target)


def _parse_keycode(args: dict[str, Any]) -> int | None:
    raw = args.get("keycode")
    if raw is None:
        raw = args.get("key")
    if raw is None:
        raw = args.get("text")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        if s.lstrip("-").isdigit():
            return int(s)
        return _KEY_NAME_TO_CODE.get(s.lower())
    return None


def _parse_key(args: dict[str, Any]) -> GuiAction:
    code = _parse_keycode(args)
    if code is None:
        raise ValueError(f"key requires a keycode: {args!r}")
    if code == 3:
        return GuiAction(action=ActionType.HOME)
    if code == 4:
        return GuiAction(action=ActionType.BACK)
    return GuiAction(action=ActionType.KEY, keycode=code)


def _parse_home(_: dict[str, Any]) -> GuiAction:
    return GuiAction(action=ActionType.HOME)


def _parse_back(_: dict[str, Any]) -> GuiAction:
    return GuiAction(action=ActionType.BACK)


def _parse_wait(args: dict[str, Any]) -> GuiAction:
    sec = _seconds(args)
    return GuiAction(action=ActionType.WAIT, seconds=sec if sec is not None else 1.0)


def _parse_system_button(args: dict[str, Any]) -> GuiAction:
    button = str(args.get("button") or "").strip().lower()
    if button == "home":
        return GuiAction(action=ActionType.HOME)
    if button == "back":
        return GuiAction(action=ActionType.BACK)
    mapping = {"menu": 82, "enter": 66}
    code = mapping.get(button)
    if code is not None:
        return GuiAction(action=ActionType.KEY, keycode=code)
    raise ValueError(f"unsupported system_button: {button!r}")


def _parse_terminate(args: dict[str, Any]) -> GuiAction:
    status = str(args.get("status") or "success")
    text = str(args.get("message") or "")
    return GuiAction(action=ActionType.TERMINATE, status=status, text=text)


def _parse_answer(args: dict[str, Any]) -> GuiAction:
    text = str(args.get("text") or "")
    return GuiAction(action=ActionType.TERMINATE, status="success", text=text)


def _parse_interact(args: dict[str, Any]) -> GuiAction:
    text = str(args.get("text") or "").strip()
    return GuiAction(action=ActionType.INTERACT, text=text)


_ACTION_PARSERS: dict[str, Any] = {
    "click": _parse_click,
    "long_press": _parse_long_press,
    "swipe": _parse_swipe,
    "scroll": _parse_swipe,
    "drag": _parse_drag,
    "type": _parse_type,
    "open": _parse_open,
    "open_app": _parse_open,
    "key": _parse_key,
    "home": _parse_home,
    "back": _parse_back,
    "wait": _parse_wait,
    "system_button": _parse_system_button,
    "terminate": _parse_terminate,
    "answer": _parse_answer,
    "interact": _parse_interact,
    "call_user": _parse_interact,
}

# Coordinate fields (by action) that adapt_action maps from the model space.
_ACTION_COORD_FIELDS: dict[ActionType, tuple[tuple[str, str], ...]] = {
    ActionType.CLICK: (("x", "y"),),
    ActionType.LONG_PRESS: (("x", "y"),),
    ActionType.SWIPE: (("x", "y"), ("x2", "y2")),
    ActionType.DRAG: (("x", "y"), ("x2", "y2")),
}


# --------------------------------------------------------------------------- #
# Backend
# --------------------------------------------------------------------------- #


class OpenAICompatBackend(GuiInferenceBackend):
    """GUI backend for any standard OpenAI-compatible chat-completions endpoint."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._model = str(config.get("model") or "")
        self._base_url = str(config.get("base_url") or "").rstrip("/")
        self._api_key = str(config.get("api_key") or "")
        self._tls_verify = bool(config.get("tls_verify", True))
        mt = config.get("max_tokens")
        self._max_tokens: int | None = int(mt) if mt is not None else None
        temp = config.get("temperature")
        self._temperature: float | None = float(temp) if temp is not None else None
        # Text history is kept in full (cheap); only screenshots are windowed to
        # the most recent `max_images` steps (including the current one).
        self._max_images = int(config.get("max_images", 5))
        self._tool_choice = str(config.get("tool_choice") or "auto").strip() or "auto"
        self._coordinate_space = str(config.get("coordinate_space") or "normalized_1000").strip()
        if self._coordinate_space not in _COORDINATE_SPACES:
            raise ValueError(
                f"unsupported coordinate_space: {self._coordinate_space!r}; "
                f"expected one of {_COORDINATE_SPACES}"
            )
        self._today_override: str | None = (
            str((config.get("prompt_meta") or {}).get("today") or "").strip() or None
        )
        self._system_prompt = build_system_prompt(
            system_prompt=str(config.get("system_prompt") or ""),
            preset=str(config.get("system_prompt_preset") or _DEFAULT_PRESET),
            coordinate_space=self._coordinate_space,
        )
        # Optional date grounding, off by default. When enabled it goes into the
        # system prompt (not crammed onto the instruction), using prompt_meta's
        # `today` when provided so runs are reproducible.
        if bool(config.get("inject_date", False)):
            self._system_prompt += _date_clause(self._today_override)
        self._tools = build_tools(self._coordinate_space)
        self._instruction = str(config.get("instruction") or "").strip()
        self._session_id = str(config.get("session_id") or uuid.uuid4())
        # history entries: {tool_calls (list of native dicts), content,
        # image_path, result_texts (list aligned to tool_calls), extra_injected}
        self._history: list[dict[str, Any]] = []
        # Number of extra_instructions entries already placed in the timeline.
        # A new append only injects the tail slice as a fresh user turn, so the
        # message prefix stays byte-stable for KV-cache reuse.
        self._injected_extra_count = 0
        self._http_client = httpx.Client(verify=self._tls_verify, timeout=120.0)
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url or None,
            http_client=self._http_client,
        )

    # -- inference -------------------------------------------------------- #

    async def infer(self, *, context: MobileGuiContext, observation: Observation) -> InferResult:
        # Backfill the previous step's execution results so the replay can pair
        # each tool call with its own role:tool reply. The executor records one
        # GuiActionResult per executed action (1:1 with that step's tool calls,
        # in order); fewer results than calls means execution stopped early
        # (soft failure / terminate), and the unexecuted tail is marked as such.
        if self._history:
            self._history[-1]["result_texts"] = [
                r.message for r in (context.last_results or [])
            ]

        instruction = self._instruction or context.task
        # extra_instructions is an append-only list. Inject only the new tail
        # as a fresh user turn this step — never fold into the prefix, which
        # would invalidate the KV-cache for every earlier turn.
        new_items = context.extra_instructions[self._injected_extra_count:]
        self._injected_extra_count = len(context.extra_instructions)
        new_extra = "\n".join(item["instruction"] for item in new_items).strip()

        messages = self._build_messages(
            current_image_path=observation.screenshot_path,
            instruction=instruction,
            current_extra=new_extra,
        )
        create_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "tools": self._tools,
        }
        if self._max_tokens is not None:
            create_kwargs["max_tokens"] = self._max_tokens
        if self._tool_choice:
            create_kwargs["tool_choice"] = self._tool_choice
        if self._temperature is not None:
            create_kwargs["temperature"] = self._temperature

        # Mirror exactly what is sent: keys absent from create_kwargs (e.g. an
        # unset max_tokens/temperature) are intentionally not logged.
        request_log = {**create_kwargs, "messages": truncate_for_log(messages)}
        self._dump_request(
            observation=observation, context=context, instruction=instruction, request_log=request_log
        )

        try:
            completion = self._client.chat.completions.create(**create_kwargs)
        except Exception as exc:
            self._dump_error(observation=observation, context=context, error=exc)
            raise

        message = completion.choices[0].message
        content = str(message.content or "")
        reasoning_content = str(getattr(message, "reasoning_content", "") or "")
        # Keep tool calls in their native OpenAI shape ({id, type, function:
        # {name, arguments}}); `arguments` stays the JSON string the API gave us.
        # It is exactly what assistant replay needs, and parse_action does the
        # one unavoidable json.loads lazily, at the point of use.
        tool_calls = [tc.model_dump() for tc in (getattr(message, "tool_calls", None) or [])]

        self._dump_response(
            observation=observation,
            context=context,
            completion=completion,
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
        )

        # Record this step into history (result_texts filled on the next infer).
        # Keep all tool calls — the model may emit several in parallel, and every
        # one needs its matching role:tool reply when this step is replayed.
        self._history.append(
            {
                "tool_calls": tool_calls,
                "content": content,
                "image_path": observation.screenshot_path,
                "result_texts": [],
                "extra_injected": new_extra,
            }
        )
        return InferResult(
            text=content,
            model_request=request_log,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
        )

    # -- parsing ---------------------------------------------------------- #

    def parse_action(self, result: InferResult) -> list[GuiAction]:
        # Standard function calling: the action lives in the structured tool
        # channel. If it is empty the model emitted a malformed/absent tool call
        # — that's the model's fault, surfaced here rather than papered over.
        if not result.tool_calls:
            raise ValueError(f"model returned no tool_calls; content={result.text[:500]!r}")

        actions: list[GuiAction] = []
        for call in result.tool_calls:
            fn = call.get("function") or {}
            name = str(fn.get("name") or "").strip().lower()
            raw_args = fn.get("arguments")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else {}
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            parser = _ACTION_PARSERS.get(name)
            if parser is None:
                raise ValueError(f"unsupported tool/action: {name!r}")
            actions.append(parser(args))
        return actions

    # -- coordinate adaptation ------------------------------------------- #

    def adapt_action(self, *, actions: list[GuiAction], observation: Observation) -> list[GuiAction]:
        if self._coordinate_space != "normalized_1000":
            return actions
        size = read_image_size(Path(observation.screenshot_path)) if observation.screenshot_path else None
        if size is None:
            return actions
        width, height = size
        return [self._scale_one(a, width, height) for a in actions]

    def _scale_one(self, action: GuiAction, width: int, height: int) -> GuiAction:
        fields = _ACTION_COORD_FIELDS.get(action.action)
        if not fields:
            return action
        updates: dict[str, int] = {}
        for fx, fy in fields:
            x = getattr(action, fx)
            y = getattr(action, fy)
            if x is None or y is None:
                continue
            updates[fx] = max(0, int(round(x / 1000.0 * width)))
            updates[fy] = max(0, int(round(y / 1000.0 * height)))
        return replace(action, **updates) if updates else action

    # -- message construction -------------------------------------------- #

    @staticmethod
    def _image_block(image_path: str) -> dict[str, Any]:
        return {"type": "image_url", "image_url": {"url": image_to_data_url(Path(image_path))}}

    def _build_messages(
        self, *, current_image_path: str, instruction: str, current_extra: str = ""
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": self._system_prompt}]
        history = self._history  # full text history; only screenshots are windowed
        n = len(history)
        # Keep screenshots only for the most recent `max_images` steps (including
        # the current one); older turns become text-only so the prefix stays
        # byte-stable. Step index runs 0..n, where n is the current step.
        image_threshold = (n + 1) - self._max_images

        def wants_image(index: int) -> bool:
            return self._max_images > 0 and index >= image_threshold

        def screenshot_part(image_path: str, index: int) -> dict[str, Any]:
            if wants_image(index):
                return self._image_block(image_path)
            return {"type": "text", "text": "(screenshot omitted)"}

        def screenshot_turn(image_path: str, index: int) -> dict[str, Any]:
            # The user turn for one step's screenshot. The instruction is the very
            # first user text, so it rides on step 0 — wherever step 0 falls (the
            # first history item, or the current step when there's no history yet).
            content: list[dict[str, Any]] = []
            if index == 0:
                content.append({"type": "text", "text": instruction})
            content.append(screenshot_part(image_path, index))
            return {"role": "user", "content": content}

        for idx, item in enumerate(history):
            # An extra_instruction that arrived at this step enters once, here, as
            # its own user turn — fixed in place on every later rebuild.
            if item.get("extra_injected"):
                messages.append({"role": "user", "content": item["extra_injected"]})

            messages.append(screenshot_turn(item["image_path"], idx))

            # Assistant turn: replay the captured tool calls verbatim — the stored
            # native shape is exactly what the assistant `tool_calls` field wants.
            # Each call needs its own role:tool reply to keep the API contract.
            calls = item.get("tool_calls") or []
            if calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": item.get("content") or None,
                        "tool_calls": calls,
                    }
                )
                result_texts = item.get("result_texts") or []
                for i, call in enumerate(calls):
                    content = result_texts[i] if i < len(result_texts) else "NOT EXECUTED"
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or "",
                            "content": content or "NO OUTPUT",
                        }
                    )
            else:
                messages.append({"role": "assistant", "content": item.get("content") or ""})

        # Current step: its own extra (if newly arrived), then the screenshot.
        if current_extra:
            messages.append({"role": "user", "content": current_extra})
        messages.append(screenshot_turn(current_image_path, n))
        return messages

    # -- dumps (best-effort) --------------------------------------------- #

    def _step_dir(self, observation: Observation) -> Path | None:
        try:
            screenshot_path = observation.screenshot_path
            if not screenshot_path:
                return None
            return Path(screenshot_path).resolve().parent
        except Exception:
            return None

    def _dump_request(
        self,
        *,
        observation: Observation,
        context: MobileGuiContext,
        instruction: str,
        request_log: dict[str, Any],
    ) -> None:
        step_dir = self._step_dir(observation)
        if step_dir is None:
            return
        try:
            step_dir.mkdir(parents=True, exist_ok=True)
            doc = {
                "step": context.step,
                "timestamp": datetime.now().astimezone().isoformat(),
                "backend": "openai_compat",
                "base_url": self._base_url,
                "session_id": self._session_id,
                "coordinate_space": self._coordinate_space,
                "history_len": len(self._history),
                "instruction": instruction,
                "extra_instructions": context.extra_instructions,
                "request": request_log,
            }
            (step_dir / "requests.json").write_text(
                json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _dump_response(
        self,
        *,
        observation: Observation,
        context: MobileGuiContext,
        completion: Any,
        content: str,
        reasoning_content: str,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        step_dir = self._step_dir(observation)
        if step_dir is None:
            return
        try:
            step_dir.mkdir(parents=True, exist_ok=True)
            usage = getattr(completion, "usage", None)
            dump_fn = getattr(usage, "model_dump", None)
            try:
                usage_dump = dump_fn() if callable(dump_fn) else usage
            except Exception:
                usage_dump = None
            try:
                finish_reason = completion.choices[0].finish_reason
            except Exception:
                finish_reason = None
            doc = {
                "step": context.step,
                "timestamp": datetime.now().astimezone().isoformat(),
                "model": getattr(completion, "model", self._model),
                "id": getattr(completion, "id", None),
                "finish_reason": finish_reason,
                "content": content,
                "reasoning_content": reasoning_content,
                "tool_calls": tool_calls,
                "usage": usage_dump,
            }
            (step_dir / "response.json").write_text(
                json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _dump_error(
        self, *, observation: Observation, context: MobileGuiContext, error: BaseException
    ) -> None:
        step_dir = self._step_dir(observation)
        if step_dir is None:
            return
        try:
            step_dir.mkdir(parents=True, exist_ok=True)
            import traceback

            doc = {
                "step": context.step,
                "timestamp": datetime.now().astimezone().isoformat(),
                "error_type": type(error).__name__,
                "error": str(error),
                "traceback": "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                ),
            }
            (step_dir / "error.json").write_text(
                json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._http_client.close()
        except Exception:
            pass
        try:
            self._client.close()
        except Exception:
            pass
