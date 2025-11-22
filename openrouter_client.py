"""Shared helpers for calling OpenRouter multimodal models."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import pathlib
import re
from typing import Any, Dict, Optional

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.5-flash"
DEFAULT_PROMPT_PATH = pathlib.Path("prompts/alt_text.md")
_ALT_PATTERN = re.compile(r"<alt>(.*?)</alt>", re.IGNORECASE | re.DOTALL)


def resolve_api_key() -> str:
    env_key = os.getenv("OPENROUTER_API_KEY")
    if not env_key:
        raise RuntimeError("OPENROUTER_API_KEY must be set in the environment")
    return env_key.strip()


def load_prompt(prompt_file: str | None, default_path: pathlib.Path = DEFAULT_PROMPT_PATH) -> str:
    path = pathlib.Path(prompt_file or default_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Prompt file is empty: {path}")
    return text


def _image_content(image_arg: str) -> Dict[str, Any]:
    if image_arg.startswith(("http://", "https://")):
        return {
            "type": "image_url",
            "image_url": {"url": image_arg},
        }

    path = pathlib.Path(image_arg).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError(f"Unsupported image mime type: {mime_type}")

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    data_url = f"data:{mime_type};base64,{encoded}"
    return {
        "type": "image_url",
        "image_url": {"url": data_url},
    }


def build_payload(prompt_text: str, image_arg: str, model: str, max_tokens: Optional[int] = None) -> Dict[str, Any]:
    content = [
        {"type": "text", "text": prompt_text},
        _image_content(image_arg),
    ]
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    return payload


def build_headers(api_key: str, referer: Optional[str] = None, title: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    referer = referer or os.getenv("OPENROUTER_REFERRER")
    if referer:
        headers["HTTP-Referer"] = referer
    title = title or os.getenv("OPENROUTER_APP_TITLE")
    if title:
        headers["X-Title"] = title
    return headers


def request_completion(
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float,
) -> Dict[str, Any]:
    with httpx.Client(timeout=timeout) as client:
        response = client.post(OPENROUTER_URL, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()


def extract_text_from_response(data: Dict[str, Any]) -> str:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message", {})
    content = message.get("content")
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if part.get("type") == "text"
        ).strip()
    return (content or "").strip()


def extract_alt_tag(text: str) -> Optional[str]:
    match = _ALT_PATTERN.search(text or "")
    if not match:
        return None
    return match.group(1).strip()


def dump_usage_info(data: Dict[str, Any]) -> str:
    usage = data.get("usage")
    if not usage:
        return ""
    return json.dumps(usage, indent=2)
