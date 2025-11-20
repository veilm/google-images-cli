import asyncio
import json
import random
import time
from contextlib import suppress
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx
import websockets
from websockets.legacy.client import WebSocketClientProtocol


def fetch_targets(http_endpoint: str):
    response = httpx.get(f"{http_endpoint.rstrip('/')}/json/list", timeout=5)
    response.raise_for_status()
    return response.json()


def format_tab(target: Dict[str, Any]) -> str:
    url = target.get("url") or "about:blank"
    title = target.get("title") or ""
    title_suffix = f"  title={title!r}" if title else ""
    return f"{url} (targetId={target.get('id')}){title_suffix}"


def find_target(endpoint: str, target_id: str) -> Dict[str, Any]:
    targets = fetch_targets(endpoint)
    for target in targets:
        if target.get("id") == target_id:
            return target
    raise SystemExit(f"No tab found with targetId={target_id}.")


SendCoroutine = Callable[[str, Optional[Dict[str, Any]]], Awaitable[Dict[str, Any]]]


class CDPClient:
    def __init__(self, websocket: WebSocketClientProtocol) -> None:
        self.websocket = websocket
        self._msg_id = 0

    async def send(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        self._msg_id += 1
        payload: Dict[str, Any] = {"id": self._msg_id, "method": method}
        if params:
            payload["params"] = params

        await self.websocket.send(json.dumps(payload))

        while True:
            raw = await self.websocket.recv()
            message = json.loads(raw)
            if "id" not in message and "method" in message:
                method_name = message["method"]
                print(f"[event] {method_name}")
                continue
            if message.get("id") == self._msg_id:
                return message


async def trigger_notification(ws_url: str, message: str) -> Dict[str, Any]:
    async with websockets.connect(ws_url) as websocket:
        client = CDPClient(websocket)
        await client.send("Runtime.enable")

        payload = json.dumps(message)
        expression = f"""
(() => {{
  const msg = {payload};
  console.log("[cdp-cli]", msg);
  const previousTitle = document.title || "";
  const newTitle = `[cdp-cli] ${{msg}}`;
  document.title = newTitle;
  return {{ previousTitle, newTitle }};
}})();
        """.strip()

        response = await client.send(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True}
        )
        return response


def websocket_session(ws_url: str):
    return websockets.connect(ws_url, max_size=None)


def next_scroll_delay(
    mean_ms: int = 500, stddev_ms: int = 150, min_ms: int = 200, max_ms: int = 5000
) -> float:
    while True:
        sample = random.gauss(mean_ms, stddev_ms)
        if min_ms <= sample <= max_ms:
            return sample / 1000


async def auto_scroll(
    send: SendCoroutine,
    stop_event: asyncio.Event,
    *,
    expression: str = "window.scrollBy(0, 40);",
    log_prefix: str = "[cdp]",
    delay_provider: Callable[[], float] = next_scroll_delay,
) -> None:
    try:
        while not stop_event.is_set():
            await asyncio.sleep(delay_provider())
            try:
                await send(
                    "Runtime.evaluate",
                    {"expression": expression, "returnByValue": False},
                )
            except Exception as exc:
                print(f"{log_prefix} Auto-scroll error: {exc}")
                break
    except asyncio.CancelledError:
        pass


async def enforce_active_state(
    send: SendCoroutine, *, log_prefix: str = "[cdp]"
) -> bool:
    try:
        await send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        await send("Page.setWebLifecycleState", {"state": "active"})
        await send(
            "Emulation.setIdleOverride",
            {"isUserActive": True, "isScreenUnlocked": True},
        )
        return True
    except Exception as exc:
        print(f"{log_prefix} Active-state enforcement error: {exc}")
        return False


async def keep_tab_focused(
    send: SendCoroutine,
    target_id: str,
    stop_event: asyncio.Event,
    *,
    interval: float = 2.0,
    log_prefix: str = "[cdp]",
) -> None:
    try:
        while not stop_event.is_set():
            try:
                await send("Target.activateTarget", {"targetId": target_id})
                await send("Page.bringToFront")
                await enforce_active_state(send, log_prefix=log_prefix)
            except Exception as exc:
                print(f"{log_prefix} Keep-focus error: {exc}")
                break
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass


async def idle_watchdog(
    stop_event: asyncio.Event,
    last_response_time_fn: Callable[[], float],
    close_coro: Callable[[], Awaitable[None]],
    *,
    idle_timeout: float = 120.0,
    check_interval: float = 5.0,
    log_prefix: str = "[cdp]",
    description: str = "responses",
    on_timeout: Optional[Callable[[str], None]] = None,
) -> None:
    try:
        while not stop_event.is_set():
            await asyncio.sleep(check_interval)
            elapsed = time.monotonic() - last_response_time_fn()
            if elapsed >= idle_timeout:
                reason_detail = f"{int(idle_timeout)}s without {description}"
                if on_timeout:
                    on_timeout(reason_detail)
                print(f"{log_prefix} Stopping: {reason_detail}.")
                stop_event.set()
                with suppress(Exception):
                    await close_coro()
                break
    except asyncio.CancelledError:
        pass
