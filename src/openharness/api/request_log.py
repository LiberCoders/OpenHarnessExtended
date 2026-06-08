"""Debug logging of complete LLM request payloads.

Every model-bound request from the OpenAI / Anthropic / Codex clients is
appended as one JSON line to ``<OPENHARNESS_LOGS_DIR>/llm_requests.jsonl``
before the actual upstream send. The recorded payload is the *full* request
that would otherwise be invisible to the user — including the system
prompt, the running message history, and the ``tools`` array — making it
possible to verify exactly what the model receives (e.g. whether
plugin-contributed tools made it into the final request).

The destination follows OH's normal logs directory resolution
(``openharness.config.paths.get_logs_dir`` — env var, then config-dir
fallback), so the file appears next to ``cron_scheduler.log`` and other
OH-managed logs.

The logger is best-effort and never raises: if writing fails, the upstream
API call proceeds unaffected.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

_LOG_FILENAME = "llm_requests.jsonl"
_RESPONSE_LOG_FILENAME = "llm_responses.jsonl"


def log_request(provider: str, payload: dict[str, Any]) -> None:
    """Append one JSON record describing a model-bound request.

    ``provider`` is a short identifier ("anthropic", "openai", "codex")
    that disambiguates payload shape — the OpenAI client embeds the system
    prompt inside ``messages``, while Anthropic uses a top-level ``system``
    field and Codex uses ``instructions``.
    """
    try:
        # Imported lazily to avoid a hard module-load dependency at
        # api package import time (paths module pulls in user config).
        from openharness.config.paths import get_logs_dir

        logs_dir = get_logs_dir()
        logs_dir.mkdir(parents=True, exist_ok=True)
        path = logs_dir / _LOG_FILENAME
        record = {
            "ts": time.time(),
            "provider": provider,
            "payload": payload,
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # pragma: no cover - never break the API call
        log.debug("Failed to append LLM request log: %s", exc)


def log_response(provider: str, payload: dict[str, Any]) -> None:
    """Append one JSON record describing a model response (best-effort)."""
    try:
        from openharness.config.paths import get_logs_dir

        logs_dir = get_logs_dir()
        logs_dir.mkdir(parents=True, exist_ok=True)
        path = logs_dir / _RESPONSE_LOG_FILENAME
        record = {
            "ts": time.time(),
            "provider": provider,
            "payload": payload,
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # pragma: no cover - never break the API call
        log.debug("Failed to append LLM response log: %s", exc)
