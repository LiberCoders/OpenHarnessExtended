"""Registry for optional GUI inference backends."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openharness.extended.experts.mobile_gui.backends.base import GuiInferenceBackend
from openharness.extended.experts.mobile_gui.backends.gui_plus_backend import GuiPlusBackend
from openharness.extended.experts.mobile_gui.backends.openai_compat_backend import OpenAICompatBackend

# Supported backend type -> factory (constructed from the config dict).
_BACKENDS: dict[str, Callable[[dict[str, Any]], GuiInferenceBackend]] = {
    "gui_plus": GuiPlusBackend,
    "openai_compat": OpenAICompatBackend,
}


def resolve_gui_backend(
    *,
    gui_backend_config: dict[str, Any] | None,
) -> GuiInferenceBackend:
    """Create a GUI backend from config.

    ``type`` is required and selects the backend (``gui_plus`` or
    ``openai_compat``). An empty or unrecognized value is an error.
    """
    cfg = dict(gui_backend_config or {})
    backend_type = str(cfg.get("type") or "").strip().lower()
    backend_cls = _BACKENDS.get(backend_type)
    if backend_cls is None:
        raise ValueError(
            f"unsupported gui_backend type: {backend_type!r}; supported: {sorted(_BACKENDS)}"
        )
    return backend_cls(cfg)
