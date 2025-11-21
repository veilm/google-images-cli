import argparse
import asyncio
import socket
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import quote_plus

import httpx

from cdp_helpers import (
    CDPClient,
    fetch_targets,
    find_target,
    format_tab,
    websocket_session,
)


def select_target(endpoint: str, target_id: Optional[str]) -> Dict:
    """Pick a tab to drive; default to the first page target."""
    if target_id:
        target = find_target(endpoint, target_id)
        print(f"Using provided target: {format_tab(target)}")
        return target

    targets = fetch_targets(endpoint)
    for target in targets:
        if target.get("type") == "page":
            print(f"Using first page target: {format_tab(target)}")
            return target
    raise SystemExit("No page targets exposed by the remote browser.")


async def navigate_and_count(endpoint: str, target_id: Optional[str], query: str) -> None:
    target = select_target(endpoint, target_id)
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise SystemExit("Selected target is missing webSocketDebuggerUrl.")

    search_url = f"https://www.google.com/search?tbm=isch&q={quote_plus(query)}"
    print(f"Connecting to {ws_url} ...")
    async with websocket_session(ws_url) as websocket:
        client = CDPClient(websocket)
        await client.send("Page.enable")
        await client.send("Runtime.enable")

        print(f"Navigating to {search_url}")
        await client.send("Page.navigate", {"url": search_url})

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 20.0
        seen_status = None
        result = None

        script = """
(() => {
  const search = document.querySelector('div#search');
  if (!search) return { status: "waiting_for_search" };
  const firstImage = search.querySelector('div[data-lpage]');
  if (!firstImage) return { status: "waiting_for_image" };
  const parent = firstImage.parentElement;
  if (!parent) return { status: "no_parent" };
  return {
    status: "ok",
    childCount: parent.children.length,
    parentTag: parent.tagName,
  };
})();
        """.strip()

        while loop.time() < deadline:
            response = await client.send(
                "Runtime.evaluate",
                {"expression": script, "returnByValue": True},
            )
            value = response.get("result", {}).get("result", {}).get("value")
            if value and value.get("status") == "ok":
                result = value
                break

            status = value.get("status") if isinstance(value, dict) else None
            if status and status != seen_status:
                print(f"Waiting for DOM elements: {status}")
                seen_status = status
            await asyncio.sleep(0.5)

    if not result:
        raise SystemExit("Timed out waiting for image container.")

    count = result.get("childCount")
    parent_tag = result.get("parentTag")
    print(f"Parent tag {parent_tag} has {count} children.")


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def launch_chromium(cmd: str, profile_dir: Path) -> Tuple[subprocess.Popen, str]:
    port = pick_free_port()
    endpoint = f"http://127.0.0.1:{port}"
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        cmd,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile_dir}",
    ]
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc, endpoint


def wait_for_endpoint(endpoint: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"{endpoint.rstrip('/')}/json/version"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise SystemExit(f"Chromium did not expose DevTools endpoint at {url} within {int(timeout)}s.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drive Chromium via CDP to inspect Google Images markup."
    )
    default_profile = Path(__file__).resolve().parent / "profiles" / "main"
    parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:2102",
        help="Base HTTP address exposing the remote debugging /json endpoints.",
    )
    parser.add_argument(
        "--launch-browser",
        action="store_true",
        help="Launch a fresh Chromium with a random debugging port for this run.",
    )
    parser.add_argument(
        "--chromium-cmd",
        default="chromium",
        help="Chromium/Chrome executable to use when --launch-browser is set.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=default_profile,
        help="User data directory for a launched browser (defaults to profiles/main).",
    )
    parser.add_argument(
        "--target-id",
        help="Optional DevTools targetId of an existing tab to reuse.",
    )
    parser.add_argument(
        "query",
        help="Search term to use for the Google Images query.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    proc: Optional[subprocess.Popen] = None
    endpoint = args.endpoint

    if args.launch_browser:
        print("Launching Chromium with remote debugging ...")
        proc, endpoint = launch_chromium(args.chromium_cmd, args.profile_dir)
        try:
            wait_for_endpoint(endpoint)
        except Exception:
            if proc:
                proc.terminate()
                proc.wait(timeout=5)
            raise
        print(f"Chromium ready at {endpoint}")

    try:
        asyncio.run(navigate_and_count(endpoint, args.target_id, args.query))
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            print("Chromium instance stopped.")


if __name__ == "__main__":
    main()
