import argparse
import asyncio
import json
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


async def highlight_failure(client: CDPClient, doc_id: Optional[str]) -> None:
    if not doc_id:
        return
    script = f"""
(() => {{
  const docId = {json.dumps(doc_id)};
  const el = document.querySelector(`div[data-docid="${{docId}}"]`);
  if (!el) return {{ found: false }};
  el.style.outline = '4px solid red';
  el.style.backgroundImage = 'repeating-linear-gradient(45deg, rgba(255,0,0,0.12), rgba(255,0,0,0.12) 10px, transparent 10px, transparent 20px)';
  const store = (window.googleImagesDL ||= {{}});
  const failures = (store.failures ||= []);
  if (!failures.includes(el)) failures.push(el);
  store.lastFailure = el;
  return {{ found: true }};
}})();
    """.strip()
    try:
        await client.send(
            "Runtime.evaluate", {"expression": script, "returnByValue": True}
        )
    except Exception:
        pass


async def navigate_and_count(
    endpoint: str,
    target_id: Optional[str],
    query: str,
    initial_wait: float,
    hover_delay: float,
    dump_html: Optional[Path],
    count: int,
    output_json: Optional[Path],
) -> bool:
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
        if initial_wait > 0:
            print(f"Waiting {initial_wait:.1f}s for page to settle ...")
            await asyncio.sleep(initial_wait)

        loop = asyncio.get_running_loop()

        def build_script(index: int) -> str:
            return f"""
(() => {{
  const search = document.querySelector('div#search');
  if (!search) return {{ status: "waiting_for_search" }};

  const items = Array.from(search.querySelectorAll('div[data-lpage]'));
  if (!items.length) return {{ status: "waiting_for_image" }};
  if ({index} >= items.length) return {{ status: "out_of_range", available: items.length }};

  const item = items[{index}];

  try {{
    item.scrollIntoView({{ block: 'center', inline: 'center', behavior: 'auto' }});
  }} catch (e) {{}}

  const parent = item.parentElement;

  const anchor =
    item.querySelector('a[href*="/imgres"]') ||
    item.closest('a[href*="/imgres"]');

  const h3 = item.querySelector('h3');
  const img = item.querySelector('img');

  const targetForHover = anchor || img || item;

  const rect = item.getBoundingClientRect ? item.getBoundingClientRect() : null;
  const rectData = rect
    ? {{
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        top: rect.top,
        left: rect.left,
        right: rect.right,
        bottom: rect.bottom,
      }}
    : null;
  const hoverRectRaw = targetForHover.getBoundingClientRect
    ? targetForHover.getBoundingClientRect()
    : null;
  const hoverRect = hoverRectRaw
    ? {{
        x: hoverRectRaw.x,
        y: hoverRectRaw.y,
        width: hoverRectRaw.width,
        height: hoverRectRaw.height,
        top: hoverRectRaw.top,
        left: hoverRectRaw.left,
        right: hoverRectRaw.right,
        bottom: hoverRectRaw.bottom,
      }}
    : null;
  const viewport = {{ scrollX: window.scrollX, scrollY: window.scrollY }};

  const anchorUrl = anchor
    ? new URL(anchor.getAttribute('href'), location.origin)
    : null;
  const params = anchorUrl ? anchorUrl.searchParams : null;
  const imgres = params
    ? {{
        href: anchorUrl.href,
        imgurl: params.get('imgurl'),
        imgrefurl: params.get('imgrefurl'),
        docid: params.get('docid'),
        tbnid: params.get('tbnid'),
        w: params.get('w'),
        h: params.get('h'),
        ved: params.get('ved'),
        vet: params.get('vet'),
      }}
    : null;

  return {{
    status: "ok",
    childCount: parent ? parent.children.length : null,
    parentTag: parent ? parent.tagName : null,
    data: {{
      landingPage: item.getAttribute('data-lpage'),
      docId: item.getAttribute('data-docid'),
      refDocId: item.getAttribute('data-ref-docid'),
      attrId: item.getAttribute('data-attrid'),
      hveid: item.getAttribute('data-hveid'),
      ivep: item.getAttribute('data-ivep'),
      alt: img ? img.getAttribute('alt') : null,
      thumbWidth: img ? img.getAttribute('width') : null,
      thumbHeight: img ? img.getAttribute('height') : null,
      h3: h3 ? h3.textContent.trim() : null,
      imgres,
    }},
    outerHTML: item.outerHTML,
    rect: rectData,
    hoverRect,
    viewport,
  }};
}})();
            """.strip()

        results = []

        for idx in range(count):
            deadline = loop.time() + 20.0
            seen_status = None
            result = None
            script = build_script(idx)

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
                if status == "out_of_range":
                    print(f"No more items available (requested index {idx}).")
                    overall = all(entry.get("success", False) for entry in results) if results else False
                    if output_json:
                        output_json.parent.mkdir(parents=True, exist_ok=True)
                        output_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
                        print(f"Wrote JSON results to {output_json}")
                    return overall
                if status and status != seen_status:
                    print(f"[{idx}] Waiting for DOM elements: {status}")
                    seen_status = status
                await asyncio.sleep(0.5)

            if not result:
                raise SystemExit("Timed out waiting for image container.")

            data = result.get("data") or {}
            doc_id = data.get("docId")

            rect = result.get("hoverRect") or result.get("rect")
            viewport = result.get("viewport") or {}
            if rect and rect.get("width") and rect.get("height"):
                target_x = viewport.get("scrollX", 0) + rect.get("left", 0) + rect.get("width", 0) / 2
                target_y = viewport.get("scrollY", 0) + rect.get("top", 0) + rect.get("height", 0) / 2
                try:
                    # Hover via synthetic mouse moves; small jitter to trigger listeners
                    await client.send("Page.bringToFront")
                    for dx, dy in [(0, 0), (1, 1), (0, 0)]:
                        await client.send(
                            "Input.dispatchMouseEvent",
                            {
                                "type": "mouseMoved",
                                "x": target_x + dx,
                                "y": target_y + dy,
                                "modifiers": 0,
                                "buttons": 0,
                                "pointerType": "mouse",
                            },
                        )
                        await asyncio.sleep(0.1)
                    # Fire JS hover events directly on the element by docId if possible
                    if doc_id:
                        doc_id_json = json.dumps(doc_id)
                        script_hover = (
                            """
(() => {
  const el = document.querySelector('div[data-docid=%s]');
  if (!el) return false;
  const targets = [];
  const a = el.querySelector('a[href*="/imgres"]');
  const img = el.querySelector('img');
  if (a) targets.push(a);
  if (img) targets.push(img);
  targets.push(el);
  for (const node of targets) {
    for (const type of ['pointerover','mouseover','mouseenter','pointermove']) {
      const evt = new Event(type, { bubbles: true });
      node.dispatchEvent(evt);
    }
  }
  return true;
})();
                            """.strip()
                            % doc_id_json
                        )
                        await client.send(
                            "Runtime.evaluate",
                            {"expression": script_hover, "returnByValue": True},
                        )
                    await asyncio.sleep(hover_delay)
                    hover_response = await client.send(
                        "Runtime.evaluate",
                        {"expression": script, "returnByValue": True},
                    )
                    hover_value = hover_response.get("result", {}).get("result", {}).get("value")
                    if hover_value and hover_value.get("status") == "ok":
                        result = hover_value
                        data = result.get("data") or {}
                except Exception as exc:
                    print(f"[warn] Hover simulation failed: {exc}")

            data = result.get("data") or {}
            doc_id = data.get("docId")
            child_count = result.get("childCount")
            parent_tag = result.get("parentTag")
            print(f"[{idx}] Parent tag {parent_tag} has {child_count} children.")
            print("Landing page:", data.get("landingPage"))
            print("Doc IDs:", {"docId": data.get("docId"), "refDocId": data.get("refDocId")})
            print("Attr IDs:", {"attrId": data.get("attrId"), "hveid": data.get("hveid"), "ivep": data.get("ivep")})
            print("Thumb:", {"width": data.get("thumbWidth"), "height": data.get("thumbHeight"), "alt": data.get("alt")})
            print("Title (h3):", data.get("h3"))
            print("imgres:", data.get("imgres"))
            success = bool(data.get("imgres"))
            if not success:
                print("[warn] No /imgres link found for selected item; saved outerHTML may help debug.")
                await highlight_failure(client, doc_id)

            dump_path: Optional[Path] = None
            if dump_html:
                if count == 1:
                    dump_path = dump_html
                else:
                    dump_path = dump_html.with_name(f"{dump_html.stem}-{idx}{dump_html.suffix}")
            elif not success:
                ts = int(time.time())
                safe_query = query.replace(" ", "_") or "query"
                dump_path = Path("captures") / f"{safe_query}_{idx}_{ts}.html"

            if dump_path:
                dump_path.parent.mkdir(parents=True, exist_ok=True)
                outer_html = result.get("outerHTML") or ""
                dump_path.write_text(outer_html, encoding="utf-8")
                print(f"Wrote element outerHTML to {dump_path}")

            results.append(
                {
                    "index": idx,
                    "success": success,
                    "data": data,
                    "childCount": child_count,
                    "parentTag": parent_tag,
                    "docId": doc_id,
                    "raw": result,
                }
            )

        if output_json:
            output_json.parent.mkdir(parents=True, exist_ok=True)
            output_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(f"Wrote JSON results to {output_json}")

        return all(entry.get("success", False) for entry in results)


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
    parser.add_argument(
        "--initial-wait",
        type=float,
        default=10.0,
        help="Seconds to wait after navigation before scraping (default: 10).",
    )
    parser.add_argument(
        "--hover-delay",
        type=float,
        default=1.0,
        help="Seconds to wait after hover simulation before scraping (default: 1).",
    )
    parser.add_argument(
        "--dump-html",
        type=Path,
        help="Optional path to save the outerHTML of scraped elements (suffixes added if multiple).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of image results to process (default: 1).",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional path to write scraped results as JSON.",
    )
    parser.add_argument(
        "--on-finish",
        choices=["close", "keep", "keep-on-error"],
        default="close",
        help="What to do with a launched browser when done: close, keep open, or keep only on error.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    proc: Optional[subprocess.Popen] = None
    run_success = False
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
        run_success = asyncio.run(
            navigate_and_count(
                endpoint,
                args.target_id,
                args.query,
                initial_wait=args.initial_wait,
                hover_delay=args.hover_delay,
                dump_html=args.dump_html,
                count=args.count,
                output_json=args.output_json,
            )
        )
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        if proc:
            should_close = True
            if args.on_finish == "keep":
                should_close = False
            elif args.on_finish == "keep-on-error" and not run_success:
                should_close = False

            if not should_close:
                print("Leaving Chromium running (--on-finish policy).")
                proc = None

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
