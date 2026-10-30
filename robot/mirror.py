"""A live mirror of Pupper's screen for a laptop: GET /mirror (the page) and WS /ws/mirror.

The app runs in Chromium on Pupper's own screen (robot/start.sh --kiosk). This relays that page's
screencast, through Chrome's debugging port on the Pi (localhost:9222, the one kiosk.py uses), to
any browser that opens /mirror, and sends its clicks and typing back, so a laptop shows and works
the same screen while there is still only one app and one Gemini session. Frames are only made
while someone watches. From a laptop: ./run_pupper.sh --laptop opens it for you.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import urllib.request

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger("mirror")

DEVTOOLS = "http://127.0.0.1:9222/json"
APP = f"localhost:{os.environ.get('PORT', '8000')}"  # the kiosk shows the app from this server
KEYS = {"Enter": 13, "Backspace": 8, "Tab": 9, "Escape": 27, "Delete": 46,
        "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40}


def kiosk_page() -> str | None:
    """The DevTools WebSocket URL of the app page on Pupper's screen, or None if it isn't up."""
    try:
        for t in json.load(urllib.request.urlopen(DEVTOOLS, timeout=2)):
            if t.get("type") == "page" and APP in t.get("url", ""):
                return t["webSocketDebuggerUrl"]
    except Exception:
        pass
    return None


async def relay(client: WebSocket) -> None:
    """One watcher: frames from Pupper's screen to the client, taps and keys back."""
    await client.accept()
    url = await asyncio.to_thread(kiosk_page)
    if not url:
        await client.send_json({"error": "Pupper's screen isn't showing the app (start it with robot/start.sh --kiosk)"})
        await client.close()
        return
    import websockets  # only here: the laptop server (run_local.sh) runs without it

    async with websockets.connect(url, max_size=None) as cdp:
        ids = itertools.count(1)
        size = {}  # the screen in CSS pixels, from each frame's metadata

        async def send(method: str, params: dict | None = None) -> None:
            await cdp.send(json.dumps({"id": next(ids), "method": method, "params": params or {}}))

        async def frames() -> None:
            async for raw in cdp:
                m = json.loads(raw)
                if m.get("method") == "Page.screencastFrame":
                    p = m["params"]
                    size.update(w=p["metadata"]["deviceWidth"], h=p["metadata"]["deviceHeight"])
                    await send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
                    await client.send_text(p["data"])  # a base64 JPEG

        async def inputs() -> None:
            while True:
                m = await client.receive_json()
                if m.get("type") == "tap" and size:
                    x, y = float(m["x"]) * size["w"], float(m["y"]) * size["h"]  # x, y: 0-1 across the image
                    await send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
                    for kind in ("mousePressed", "mouseReleased"):
                        await send("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})
                elif m.get("type") == "key":
                    key = str(m.get("key", ""))
                    if key in KEYS:
                        for kind in ("rawKeyDown", "keyUp"):
                            await send("Input.dispatchKeyEvent", {"type": kind, "key": key, "windowsVirtualKeyCode": KEYS[key]})
                    elif len(key) == 1:
                        await send("Input.insertText", {"text": key})

        await send("Page.startScreencast", {"format": "jpeg", "quality": 60, "maxWidth": 1024, "maxHeight": 1024})
        log.info("a laptop is watching Pupper's screen")
        tasks = [asyncio.create_task(frames()), asyncio.create_task(inputs())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:  # the laptop left (WebSocketDisconnect) or the kiosk page went away
                if t.exception() and not isinstance(t.exception(), WebSocketDisconnect):
                    log.info("mirror ended: %s", t.exception())
        finally:
            for t in tasks:
                t.cancel()
            try:
                await send("Page.stopScreencast")
            except Exception:
                pass
            log.info("the laptop stopped watching Pupper's screen")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pupper Mirror</title>
<style>
  :root { --bg: #000; --fg: #ddd; }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--fg); font: 16px system-ui, sans-serif; }
  body { display: flex; align-items: center; justify-content: center; overflow: hidden; }
  img { display: block; max-width: 100vw; max-height: 100vh; cursor: pointer; user-select: none; }
  #status { position: fixed; top: 12px; left: 16px; right: 16px; opacity: .8; }
</style></head>
<body>
<div id="status">Connecting to Pupper's screen...</div>
<img id="screen" alt="Pupper's screen" draggable="false">
<script>
const img = document.getElementById('screen'), status = document.getElementById('status');
let ws;
function connect() {
  ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/mirror');
  ws.onmessage = (e) => {
    if (e.data[0] === '{') { status.textContent = JSON.parse(e.data).error; return; }
    img.src = 'data:image/jpeg;base64,' + e.data;
    status.textContent = '';
  };
  ws.onclose = () => { if (!status.textContent) status.textContent = 'Lost Pupper\\'s screen; reconnecting...'; setTimeout(connect, 2000); };
}
img.addEventListener('click', (e) => {
  const r = img.getBoundingClientRect();
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'tap', x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height }));
});
document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || !ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify({ type: 'key', key: e.key }));
  e.preventDefault();
});
connect();
</script>
</body></html>
"""
