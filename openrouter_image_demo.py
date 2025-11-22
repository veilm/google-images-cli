"""Simple script that sends an image + prompt to an OpenRouter vision model."""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import pathlib
import sys
from typing import Any, Dict

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.5-flash"
DEFAULT_PROMPT_PATH = pathlib.Path("prompts/alt_text.md")


def _resolve_api_key() -> str:
    env_key = os.getenv("OPENROUTER_API_KEY")
    if not env_key:
        raise RuntimeError("OPENROUTER_API_KEY must be set in the environment")
    return env_key.strip()


def _load_prompt(prompt_file: str | None) -> str:
    path = pathlib.Path(prompt_file or DEFAULT_PROMPT_PATH).expanduser()
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


def _build_payload(args: argparse.Namespace) -> Dict[str, Any]:
    content = [
        {"type": "text", "text": _load_prompt(args.prompt_file)},
        _image_content(args.image),
    ]
    payload: Dict[str, Any] = {
        "model": args.model,
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
    }
    if args.max_tokens:
        payload["max_tokens"] = args.max_tokens
    return payload


def _headers(api_key: str, args: argparse.Namespace) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    referer = args.referer or os.getenv("OPENROUTER_REFERRER")
    if referer:
        headers["HTTP-Referer"] = referer
    title = args.title or os.getenv("OPENROUTER_APP_TITLE")
    if title:
        headers["X-Title"] = title
    return headers


def run_demo(args: argparse.Namespace) -> None:
    api_key = _resolve_api_key()
    payload = _build_payload(args)
    headers = _headers(api_key, args)

    with httpx.Client(timeout=args.timeout) as client:
        response = client.post(OPENROUTER_URL, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()

    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})
    content = message.get("content")
    if isinstance(content, list):
        text = "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
    else:
        text = content or ""

    print("Model response:\n")
    print(text.strip() or json.dumps(message, indent=2))

    usage = data.get("usage")
    if usage:
        print("\nToken usage:")
        print(json.dumps(usage, indent=2))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send an image + prompt to an OpenRouter multimodal model.",
    )
    parser.add_argument("image", help="Image URL or local path")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenRouter model ID (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Optional max_tokens override",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="HTTP timeout in seconds",
    )
    parser.add_argument(
        "--referer",
        default=None,
        help="Optional HTTP-Referer header (recommended if you have a website)",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Optional X-Title header shown in the OpenRouter dashboard",
    )
    parser.add_argument(
        "--prompt-file",
        default=str(DEFAULT_PROMPT_PATH),
        help=f"Path to prompt file (default: {DEFAULT_PROMPT_PATH})",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    run_demo(_parse_args(sys.argv[1:]))
