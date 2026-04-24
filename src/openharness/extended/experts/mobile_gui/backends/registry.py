"""Registry for optional GUI inference backends."""

from __future__ import annotations

from typing import Any

from openharness.extended.experts.mobile_gui.backends.base import GuiInferenceBackend
from openharness.extended.experts.mobile_gui.backends.gui_plus_backend import GuiPlusBackend


def resolve_gui_backend(
    *,
    gui_backend_config: dict[str, Any] | None,
) -> GuiInferenceBackend:
    """Create GUI backend from config; only gui_plus is supported."""
    cfg = dict(gui_backend_config or {})
    backend_type = str(cfg.get("type") or "").strip().lower()
    if backend_type and backend_type != "gui_plus":
        raise ValueError(f"unsupported gui_backend type: {backend_type!r}; only 'gui_plus' is supported")
    cfg.setdefault("type", "gui_plus")
    return GuiPlusBackend(cfg)

