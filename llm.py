"""Thin Ollama /api/chat wrapper for the ReAct agent."""

from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1:8b"


def ollama_host() -> str:
    return os.getenv("OLLAMA_HOST", DEFAULT_HOST).rstrip("/")


def ollama_model() -> str:
    return os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.1,
    timeout_s: float | None = None,
) -> str:
    """Send a chat completion to Ollama and return the assistant message content."""
    host = ollama_host()
    chosen_model = model or ollama_model()
    if timeout_s is None:
        timeout_s = float(os.getenv("OLLAMA_TIMEOUT_S", "120"))

    payload: dict[str, Any] = {
        "model": chosen_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }

    url = f"{host}/api/chat"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Cannot reach Ollama at {host}. "
            "Set OLLAMA_HOST (e.g. http://localhost:11434 or "
            "http://host.docker.internal:11434) and ensure the model is pulled."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Ollama chat failed ({exc.response.status_code}): {exc.response.text}"
        ) from exc

    data = response.json()
    message = data.get("message") or {}
    content = message.get("content")
    if not content:
        raise RuntimeError(f"Ollama returned empty content: {data!r}")
    return str(content).strip()
