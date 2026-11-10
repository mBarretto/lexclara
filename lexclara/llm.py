"""Thin wrapper around the Claude API with a record/replay cache.

Every agent call goes through :func:`call`. The request is hashed; the
validated structured response is stored as JSON under the cache directory.
This makes the demo reproducible: the repository ships the recorded
responses, so ``lexclara demo --replay`` runs end to end with no API key,
and anyone with a key can re-record them with ``--live``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal, Type, TypeVar

import anthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def _load_dotenv(path: Path) -> None:
    """Read KEY=value lines from a local .env (git-ignored) without overriding the environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MODEL = os.environ.get("LEXCLARA_MODEL", "claude-opus-5-5")
CACHE_DIR = Path(os.environ.get("LEXCLARA_CACHE", Path(__file__).resolve().parent.parent / "examples" / "cache"))
Mode = Literal["auto", "live", "replay"]
MODE: Mode = os.environ.get("LEXCLARA_MODE", "auto")  # type: ignore[assignment]

_client: anthropic.Anthropic | None = None


class ReplayMiss(RuntimeError):
    pass


class Refused(RuntimeError):
    pass


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _key(model: str, system: list[dict], prompt: str, schema: Type[BaseModel], effort: str) -> str:
    payload = json.dumps(
        {"model": model, "system": system, "prompt": prompt,
         "schema": schema.model_json_schema(), "effort": effort},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def call(*, agent: str, document_context: str, instructions: str, prompt: str,
         schema: Type[T], effort: str = "medium", mode: Mode | None = None) -> T:
    """One structured call to Claude.

    The system prompt has two parts. The first, shared by all agents, holds
    the whole document and is marked for prompt caching, so the per-block
    calls of every agent reuse the same cached prefix. The second part holds
    the agent's own instructions.
    """
    mode = mode or MODE
    system = [
        {"type": "text", "text": document_context, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": instructions},
    ]
    key = _key(MODEL, system, prompt, schema, effort)
    path = CACHE_DIR / f"{agent}-{key}.json"

    if mode != "live" and path.exists():
        return schema.model_validate_json(path.read_text(encoding="utf-8"))
    if mode == "replay":
        raise ReplayMiss(f"No recorded response for {agent} ({path.name}). Run with --live to record it.")

    response = _get_client().beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_format=schema,
        output_config={"effort": effort},
        thinking={"type": "adaptive"},
        # If a safety classifier declines, the API retries on a fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise Refused(f"{agent}: the model declined this request.")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise RuntimeError(f"{agent}: response was truncated or did not match the schema.")

    result: T = response.parsed_output
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
