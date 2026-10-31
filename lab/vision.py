"""Blind mode's senses: what the eyes service sees, and when the way is blocked.

The eyes service (vision/eyes.py, on the Pi or a laptop) watches the camera and posts to the robot
server, which keeps the latest here:

  VISION      the COCO detector's boxes (POST /vision/detections): {label, score, box}, box = [x, y, w, h]
              as fractions of the frame, x to the right, y down.
  FREESPACE   the depth sense (POST /vision/freespace): for five strips of the view cut by bearing,
              where the nearest thing standing on the floor meets the floor, as Lab 5's row
              (``nearness``, lab/fisheye_geom.py). Also the fisheye's image disc (``circle``).

path_blocked() is the obstacle rule find uses while it walks (lab/blind_mode.py): a close thing the
detector can name in the way ("a chair is right in front of me"), else anything the depth sense sees
close in the way ("something low"). It runs only inside find's own walking; the lab's move, turn and
arc are unchanged. Stale data (nothing posted lately) means blind: the rule stays out of the way,
and find, seeing no depth sense, only turns.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request

log = logging.getLogger("vision")

FRESH_S = 2.0  # detections older than this are no eyes at all
MIN_SCORE = 0.4
VISION: dict = {"time": 0.0, "detections": [], "width": 0, "height": 0}

# Free space: five strips, left to right, cut by bearing (the camera is a 180-degree fisheye, so the
# eyes service cuts them at -45, -15, +15 and +45 degrees): "ahead" is the middle three, within 45
# degrees of straight on, which is what a forward step can hit. Each strip: nearness (Lab 5's row of
# the nearest base), closest_m (metres; the desk tests post that form) or near_frac (a relative model).
STRIPS = ["left", "center-left", "center", "center-right", "right"]
AHEAD = {"center-left", "center", "center-right"}
LEFT = {"left", "center-left"}
RIGHT = {"center-right", "right"}
# The detector's "ahead" is the same: a box whose bearing is within AHEAD_DEG of straight on. (The box's
# centre between 0.25 and 0.75 of the frame's width, as the first version had it, spans about +-90
# degrees on this fisheye, so a person standing beside the robot blocked every forward walk.)
AHEAD_DEG = 45.0
FREESPACE_FRESH_S = 2.5  # a depth summary older than this (counting from its frame) is not trusted
STOP_M = 0.45  # metric posts only: nothing closer than this in the direction of a move
CAM_HEIGHT_STANDING_M = 0.22  # the camera's height, lying and standing, for the eyes service's
CAM_HEIGHT_LYING_M = 0.12  # rough metres (not measured; the rules use Lab 5's rows)
# Lab 5's row of a thing's base (0.5 is the horizon, fisheye_geom.STOP_NEARNESS 0.794 is where the
# Lab 5 follower stops at a person's feet): a base at BLOCK_NEARNESS or lower in the image is in the
# way. Measured on the robot: a backpack at its feet 0.71, a cable hanging in front 0.74, chairs a metre off 0.54.
BLOCK_NEARNESS = 0.68
NEAR_FRAC_BLOCK = 0.45  # relative: the near surface fills this much of the lower band
WHOLE_FRAME = [0.5, 0.5, 0.5, 0.5]  # the image disc until an eyes service has measured the real one
FREESPACE: dict = {"time": 0.0, "strips": [], "mode": "", "eyes_url": "", "circle": list(WHOLE_FRAME), "source": ""}
# The detector runs on the whole frame, black border and all, and on the bright disc in it it sometimes
# sees one huge "person" (score 0.96) reaching well out into the black: seen on the robot's debug view,
# where it blocked every forward walk. Real things are inside the disc: a box counts only if at least
# MIN_IN_LENS of it is (false boxes measured 0.36-0.45 inside, real ones 0.62-1.0, a person filling the view 0.8).
MIN_IN_LENS = 0.55

# Attention: the eyes service runs at full rate only while it matters (a motion running or queued,
# follow mode on, or a search started or the debug page polled in the last ATTENTION_S) and idles
# otherwise, which keeps the Pi cool. Its posts' replies and GET /vision say so.
ATTENTION_S = 10.0
ATTENTION: dict = {"until": 0.0}
# Who is posting free space (the Pi's own eyes, a laptop's): a poster that sees another fresh source stands down.
POSTERS: dict[str, float] = {}
OTHER_FRESH_S = 2.0
EYES_ALIVE_S = 15.0  # an eyes service that posted within this is alive (idle, it posts every 5 s)
EYES_TIMEOUT_S = 25.0

# What people call things -> what the COCO detector calls them. Anything else is matched by name.
SYNONYMS = {
    "phone": "cell phone", "mobile": "cell phone", "iphone": "cell phone", "mug": "cup", "glass": "cup",
    "water bottle": "bottle", "ball": "sports ball", "tennis ball": "sports ball", "computer": "laptop",
    "bag": "backpack", "purse": "handbag", "controller": "remote", "tv remote": "remote", "television": "tv",
    "monitor": "tv", "screen": "tv", "someone": "person", "somebody": "person", "people": "person",
    "human": "person", "man": "person", "woman": "person", "kid": "person", "seat": "chair", "sofa": "couch",
    "plant": "potted plant", "bike": "bicycle", "puppy": "dog", "kitty": "cat", "notebook": "book",
}
COCO_NAMES = {
    "person", "bicycle", "car", "motorcycle", "bus", "train", "truck", "boat", "bench", "bird", "cat", "dog", "backpack",
    "umbrella", "handbag", "suitcase", "sports ball", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana",
    "apple", "orange", "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
}
# Classes the detector hedges between (the fisheye distorts things): asked for one, its look-alikes count too.
LOOKALIKES = {
    "backpack": {"suitcase", "handbag"}, "handbag": {"backpack", "suitcase"}, "suitcase": {"backpack", "handbag"},
    "chair": {"couch", "bench"}, "couch": {"chair", "bed"}, "cup": {"bowl", "wine glass"}, "bottle": {"cup", "vase"},
    "sports ball": {"apple", "orange", "frisbee"}, "laptop": {"tv", "keyboard"}, "tv": {"laptop"},
    "cell phone": {"remote"}, "remote": {"cell phone"}, "dog": {"cat", "teddy bear"}, "cat": {"dog", "teddy bear"},
}


# ============================================================ what the eyes service posts
def update_detections(detections: list, width: int = 0, height: int = 0, frame_age_s: float = 0.0) -> int:
    """The detector's latest boxes. Bad entries are dropped, never a crash. ``frame_age_s``: how long ago
    their frame was taken (the Pi takes about a second), so VISION["time"] is when they were there."""
    clean = []
    for d in detections or []:
        try:
            box = [max(0.0, min(1.0, float(v))) for v in d["box"]][:4]
            if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
                continue
            clean.append({"label": str(d["label"]).lower()[:40], "score": float(d.get("score", 1.0)), "box": box})
        except (KeyError, TypeError, ValueError):
            continue
    try:
        age = max(0.0, min(10.0, float(frame_age_s or 0.0)))
    except (TypeError, ValueError):
        age = 0.0
    VISION.update(time=time.time() - age, detections=clean, width=int(width or 0), height=int(height or 0))
    return len(clean)


def update_freespace(strips: list, mode: str = "relative", eyes_url: str = "", circle: list | None = None,
                     frame_age_s: float = 0.0, source: str = "") -> int:
    """The eyes service's latest free-space summary. Bad strips are dropped, never a crash."""
    clean = []
    for st in strips or []:
        try:
            name = str(st["name"])
            if name not in STRIPS:
                continue
            closest = st.get("closest_m")
            near = st.get("near_frac")
            nearness = st.get("nearness")
            clean.append({"name": name,
                          "closest_m": None if closest is None else max(0.0, float(closest)),
                          "near_frac": None if near is None else max(0.0, min(1.0, float(near))),
                          "nearness": None if nearness is None else max(0.0, min(1.0, float(nearness)))})
        except (KeyError, TypeError, ValueError):
            continue
    try:
        age = max(0.0, min(30.0, float(frame_age_s or 0.0)))
    except (TypeError, ValueError):
        age = 0.0
    source = str(source or "")[:40]
    FREESPACE.update(time=time.time() - age, strips=clean, mode=str(mode or "relative")[:16], source=source)
    POSTERS[source] = time.time() - age
    if eyes_url:  # an eyes service without Gemma (the Pi's own) posts none, so a laptop's stays known
        FREESPACE["eyes_url"] = str(eyes_url)[:200]
    try:
        vals = [float(v) for v in (circle or [])]
        cx, cy, r = vals[:3]
        rh = vals[3] if len(vals) > 3 else r  # the radius as a fraction of the height, if the eyes service says
        if 0.2 <= cx <= 0.8 and 0.2 <= cy <= 0.8 and 0.15 <= r <= 0.5 and 0.15 <= rh <= 0.6:
            FREESPACE["circle"] = [cx, cy, r, rh]
    except (TypeError, ValueError):
        pass
    return len(clean)


def wake(seconds: float = ATTENTION_S) -> None:
    """Something needs the eyes (a search, the debug page): look properly for a while."""
    ATTENTION["until"] = max(ATTENTION["until"], time.time() + seconds)


def attention_now(robot=None) -> bool:
    """Should the eyes run at full rate right now?"""
    if time.time() < ATTENTION["until"]:
        return True
    if robot is None:
        return False
    try:
        return bool(robot.following or robot.current is not None or not robot.commands.empty())
    except Exception:
        return False


def eyes_alive() -> bool:
    """Is any eyes service posting at all (idle posts count)? The data may still be stale at this
    instant: that is the gating, not a fault, and the eyes wake within half a second of a search."""
    return any(time.time() - t <= EYES_ALIVE_S for t in POSTERS.values())


def others_fresh(source: str) -> bool:
    """Has a different eyes service posted free space within OTHER_FRESH_S?"""
    now = time.time()
    return any(src != source and now - t <= OTHER_FRESH_S for src, t in POSTERS.items())


def camera_height_m(robot) -> float:
    """How high the camera is right now: lying (motors idle) or standing (anything else)."""
    from robot_api import MOTORS_IDLE

    try:
        lying = robot.state_now is MOTORS_IDLE
    except Exception:
        lying = False
    return CAM_HEIGHT_LYING_M if lying else CAM_HEIGHT_STANDING_M


# ============================================================ the depth sense
def fresh_freespace() -> list[dict] | None:
    """The strips if the depth summary is recent, else None (no depth sense: no rule applies)."""
    if time.time() - FREESPACE["time"] > FREESPACE_FRESH_S:
        return None
    return FREESPACE["strips"]


def strip_blocked(st: dict) -> str | None:
    """Why this strip counts as blocked, or None. The eyes service posts nearness (Lab 5's row of
    the base, calibrated); closest_m is the metric form the desk tests use."""
    n = st.get("nearness")
    if n is not None:
        from fisheye_geom import STOP_NEARNESS

        if n >= STOP_NEARNESS:
            return "right at my feet"
        return "very close" if n >= BLOCK_NEARNESS else None
    if st.get("closest_m") is not None:
        return f"about {st['closest_m']:.1f} m away" if st["closest_m"] <= STOP_M else None
    if st.get("near_frac") is not None and st["near_frac"] >= NEAR_FRAC_BLOCK:
        return "very close (low, by my depth sense)"
    return None


def freespace_blocked(vx: float, vy: float) -> str | None:
    """None if the depth sense shows nothing close in the way of a move at (vx, vy), else why not.
    Class-agnostic: it doesn't know what the thing is, only that it is there."""
    strips = fresh_freespace()
    if not strips:
        return None
    wanted: set[str] = set()
    if vx > 0:
        wanted |= AHEAD
    if vy > 0:
        wanted |= LEFT
    if vy < 0:
        wanted |= RIGHT
    hits = [(st, why) for st in strips if st["name"] in wanted for why in [strip_blocked(st)] if why]
    if not hits:
        return None
    st, why = max(hits, key=lambda h: (h[0].get("nearness") or 0.0, -(h[0]["closest_m"] if h[0]["closest_m"] is not None else 9.0)))
    where = "right in front of me" if st["name"] == "center" else f"ahead on my {'left' if st['name'] in LEFT else 'right'}" \
        if st["name"] in AHEAD else f"on my {'left' if st['name'] in LEFT else 'right'}"
    return (f"blocked: something low is {where}, {why}; my depth sense sees it even though I can't name it"
            " (a chair leg, a cable, a shoe?). I won't walk into it")


# ============================================================ the detector
def in_lens(box: list) -> float | None:
    """How much of a box (0..1) is inside the camera's image disc, as the eyes service measured it.
    None until it has (no eyes service): then every box counts."""
    c = FREESPACE.get("circle") or WHOLE_FRAME
    if list(c) == WHOLE_FRAME:
        return None
    cx, cy, r, rh = c
    x, y, w, h = box
    n = 10
    inside = sum(((x + (i + 0.5) / n * w - cx) / r) ** 2 + ((y + (j + 0.5) / n * h - cy) / rh) ** 2 <= 1.0
                 for i in range(n) for j in range(n))
    return inside / (n * n)


def counts(d: dict) -> bool:
    """Does a detection count at all: sure enough, and mostly inside the image disc (not the black border)?"""
    frac = in_lens(d["box"])
    return d["score"] >= MIN_SCORE and (frac is None or frac >= MIN_IN_LENS)


def fresh() -> list[dict] | None:
    """The detections that count, if they are recent, else None (blind: no rule applies)."""
    if time.time() - VISION["time"] > FRESH_S:
        return None
    return [d for d in VISION["detections"] if counts(d)]


def how_near(n: float | None) -> str:
    """Lab 5's row of a thing's base, in words a person understands."""
    from fisheye_geom import SLOW_NEARNESS, STOP_NEARNESS

    if n is None:
        return "at an unknown distance"
    if n >= STOP_NEARNESS:
        return "right at my feet"
    if n >= BLOCK_NEARNESS:
        return "very close"
    if n >= SLOW_NEARNESS:
        return "close"
    return "far away" if n < 0.55 else "a few steps away"


def side_of(d: dict) -> str:
    from fisheye_geom import box_bearing_deg

    b = box_bearing_deg(d["box"])
    return "to the left" if b < -12 else "to the right" if b > 12 else "straight ahead"


def how_close(d: dict) -> str:
    """How near a detected thing is: Lab 5's rule on its box's bottom edge (its base on the floor),
    with the box's size as the fallback for a box the lens model can't place."""
    from fisheye_geom import box_nearness

    n = box_nearness(d["box"])
    if n is not None:
        return how_near(n)
    x, y, w, h = d["box"]
    if h >= 0.6 or w >= 0.7:
        return "very close"
    if h >= 0.4 or (y + h >= 0.9 and w * h >= 0.08):
        return "close"
    return "a few steps away" if h >= 0.2 else "far away"


def is_close(d: dict) -> bool:
    """In the way if walked into: its base at BLOCK_NEARNESS or lower in the image (the row that
    blocks a walk), or, for a box the lens model can't place, big in the frame."""
    from fisheye_geom import box_nearness

    n = box_nearness(d["box"])
    if n is not None:
        return n >= BLOCK_NEARNESS
    return how_close(d) in ("very close", "close")


def in_ahead(d: dict) -> bool:
    """Is a detection where a forward step can hit it: within AHEAD_DEG of straight on (by its bearing)?"""
    from fisheye_geom import box_bearing_deg

    return abs(box_bearing_deg(d["box"])) <= AHEAD_DEG


def detection_blocked(vx: float, vy: float) -> str | None:
    """The detector's rule: a known thing, close, in the way (or None)."""
    dets = fresh()
    if not dets:
        return None
    for d in sorted(dets, key=lambda d: -d["box"][3]):  # the biggest (closest) first
        if not is_close(d):
            continue
        cx = d["box"][0] + d["box"][2] / 2
        ahead = in_ahead(d)
        if (vx > 0 and ahead) or (vy > 0 and cx < 0.4) or (vy < 0 and cx > 0.6):
            where = "right in front of me" if ahead else f"on my {'left' if cx < 0.5 else 'right'}"
            return f"blocked: a {d['label']} is {where}, {how_close(d)}; I won't walk into it"
    return None


def path_blocked(vx: float, vy: float) -> str | None:
    """None if nothing close is in the way of a move at (vx, vy), else why not. The detector names
    what it knows (a chair, a person); the depth sense catches anything else without naming it."""
    return detection_blocked(vx, vy) or freespace_blocked(vx, vy)


# ============================================================ is a thing in view?
def matches(wanted: str, label: str) -> bool:
    """Does the detector's label count as what the person asked for (synonyms and look-alikes too)?"""
    wanted = SYNONYMS.get(wanted, wanted)
    if label in LOOKALIKES.get(wanted, set()):
        return True
    return wanted == label or wanted in label.split() or label in wanted.split() or wanted == label.replace(" ", "")


def detector_knows(name: str) -> bool:
    return any(matches(name, c) for c in COCO_NAMES)


def visible(name: str, since: float = 0.0) -> dict | None:
    """The best fresh detection of ``name`` (seen after ``since``), or None."""
    if VISION["time"] < since:
        return None
    hits = [d for d in fresh() or [] if matches(name, d["label"])]
    return max(hits, key=lambda d: d["score"]) if hits else None


def eyes_url() -> str:
    """Where the eyes service is: $PUPPER_EYES_URL, else what it said when it last posted free space."""
    return os.environ.get("PUPPER_EYES_URL", "").rstrip("/") or FREESPACE.get("eyes_url", "").rstrip("/")


def ask_eyes(url: str, path: str, payload: dict, timeout: float = EYES_TIMEOUT_S) -> dict:
    """POST json to the eyes service and return its json (blocking: call it in a thread)."""
    req = urllib.request.Request(url + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


async def seen_now(name: str) -> dict | None:
    """Is the thing in view right now? The detector's word, else the eyes service's /exists (Gemma,
    for a thing the detector has no class for, or misses). None when not seen, or no eyes."""
    hit = visible(name)
    if hit:
        return {"source": "detector", "box": hit["box"]}
    url = eyes_url()
    if not url:
        return None
    try:
        reply = await asyncio.to_thread(ask_eyes, url, "/exists", {"name": name}, 20.0)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        log.warning("eyes /exists failed: %s", e)
        return None
    if reply.get("found"):
        return {"source": reply.get("source", "eyes"), "box": reply.get("box")}
    return None
