"""On-disk state layout for heterogeneous agent workers (meta / steps / result)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MobileGuiStateStore:
    """JSON artifact store under one agent_id directory (used by mobile_gui and any type sharing this layout)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.steps_dir = self.root / "steps"
        self.root.mkdir(parents=True, exist_ok=True)
        self.steps_dir.mkdir(parents=True, exist_ok=True)

    def save_meta(self, payload: dict[str, Any]) -> None:
        (self.root / "meta.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def save_step(self, step: int, payload: dict[str, Any]) -> None:
        step_dir = self.steps_dir / f"step_{step:04d}"
        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / "step.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with (self.root / "index.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def save_result(self, payload: dict[str, Any]) -> None:
        (self.root / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

