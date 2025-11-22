"""Simple script that sends an image + prompt to an OpenRouter vision model."""
from __future__ import annotations

import argparse
import json
import sys

from openrouter_client import (
    DEFAULT_MODEL,
    DEFAULT_PROMPT_PATH,
    build_headers,
    build_payload,
    dump_usage_info,
    extract_text_from_response,
    load_prompt,
    request_completion,
    resolve_api_key,
)


def run_demo(args: argparse.Namespace) -> None:
    api_key = resolve_api_key()
    prompt_text = load_prompt(args.prompt_file, DEFAULT_PROMPT_PATH)
    payload = build_payload(prompt_text, args.image, args.model, args.max_tokens)
    headers = build_headers(api_key, args.referer, args.title)

    data = request_completion(payload, headers, args.timeout)
    text = extract_text_from_response(data)

    print("Model response:\n")
    print(text or json.dumps(data.get("choices", [{}])[0], indent=2))

    usage = dump_usage_info(data)
    if usage:
        print("\nToken usage:")
        print(usage)


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
