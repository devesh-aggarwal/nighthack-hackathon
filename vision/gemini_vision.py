"""Is someone collapsed on the floor? Asks Gemini (a fast, non-live model) about one camera frame.

The robot server makes one of these as robot.vision (see make_vision in robot/server.py), and
lab/collapse_watch.py calls look() every few seconds with Pupper's latest camera frame. Each call
sends that one JPEG to Google's Gemini API and gets back a small JSON answer:

    {"down": true, "x": 0.31, "nearest_m": 2.0, "path_clear": true, "why": "a person face down on the rug"}

down: someone is lying or slumped on the floor and may have collapsed. x: where they are across
the image (0 = left edge, 1 = right edge). nearest_m: about how far the nearest part of any
person's body is (feet and legs too). path_clear: the floor straight ahead, for about the next
metre, has no body part or object on it. Pupper only walks forward when path_clear is true. The
camera can't measure distance, so these are Gemini's estimates: collapse_watch.py keeps a wide
margin and looks again after every short step.

Only the standard library: no google-genai needed. The key comes from GEMINI_API_KEY or
~/.config/gemini-pupper/api_key (robot/setup.sh writes it); it is sent in a header, never logged.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import urllib.request
from pathlib import Path

log = logging.getLogger("vision")

KEY_FILE = Path.home() / ".config" / "gemini-pupper" / "api_key"
MODEL = os.environ.get("PUPPER_VISION_MODEL", "gemini-flash-latest")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_S = 10.0

QUESTION = (
    "This is a frame from the camera of a small robot dog, about 20 cm above the floor, through a"
    " wide-angle fisheye lens. Is there a person lying or slumped on the floor or ground who may have"
    " collapsed, fainted or fallen? Count only a person on the floor. Do NOT count someone standing,"
    " walking, kneeling, sitting (on the floor or on a chair), lying on a bed or a sofa, or clearly"
    " exercising or playing (push-ups, yoga, stretching). If there is one: x is the horizontal centre"
    " of that person in the image, from 0 (left edge) to 1 (right edge). If not: down is false and x"
    " is 0.5. Always: nearest_m is about how far, in metres, the nearest part of ANY person's body"
    " (feet, legs, hands, head) is from the camera (99 if no person is visible); be cautious and"
    " rather say too little than too much. path_clear is true only if the floor straight ahead of the"
    " robot (the bottom middle of the image) is free of any part of a person's body and of any"
    " object for about the next metre; if unsure, false. why: a few words on what you see."
)
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "down": {"type": "BOOLEAN"},
        "x": {"type": "NUMBER"},
        "nearest_m": {"type": "NUMBER"},
        "path_clear": {"type": "BOOLEAN"},
        "why": {"type": "STRING"},
    },
    "required": ["down", "x", "nearest_m", "path_clear", "why"],
}


def api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    return key or None


class GeminiVision:
    def __init__(self, key: str, model: str = MODEL) -> None:
        self.key, self.model = key, model
        self.failures = 0

    async def look(self, jpeg: bytes) -> dict | None:
        """What Gemini sees in this frame, or None if it couldn't be asked (offline, bad key)."""
        try:
            return await asyncio.to_thread(self._ask, jpeg)
        except Exception as e:  # never crash the watcher; say so now and then
            self.failures += 1
            if self.failures in (1, 10) or self.failures % 100 == 0:
                log.warning("couldn't ask %s about the camera frame (%d times): %s", self.model, self.failures,
                            f"{type(e).__name__}: {e}")
            return None

    def _ask(self, jpeg: bytes) -> dict:
        body = {
            "contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()}},
                {"text": QUESTION},
            ]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SCHEMA},
        }
        req = urllib.request.Request(URL.format(model=self.model), data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            reply = json.load(r)
        text = "".join(p.get("text", "") for p in reply["candidates"][0]["content"]["parts"])
        seen = json.loads(text)
        self.failures = 0
        return {"down": bool(seen.get("down")), "x": min(1.0, max(0.0, float(seen.get("x", 0.5)))),
                "nearest_m": max(0.0, float(seen.get("nearest_m", 0.0))),  # missing: 0, too close to walk
                "path_clear": seen.get("path_clear") is True, "why": str(seen.get("why", ""))}
