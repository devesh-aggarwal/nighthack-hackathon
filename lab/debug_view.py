"""The debug view: what Pupper sees, and why it won't walk (blind mode's senses, lab/vision.py).

Open http://localhost:8000/debug (on the robot's screen, or a laptop through the SSH tunnel). It
shows the camera live with what the rules are working from drawn on top, and next to it the verdict
the obstacle rule gives a forward walk right now, sense by sense:

  - every detection: label, score, bearing, the row of its base (Lab 5's nearness), and whether it
    counts as close, as ahead, and so as blocking a forward walk (detection_blocked);
  - the depth sense's five strips, each drawn at the row of its nearest base, red when it blocks
    (freespace_blocked);
  - the lines the rules use: STOP_BOTTOM (arrival, Lab 5's 454 of 572), BLOCK_NEARNESS (a walk is
    refused) and the slowdown row, drawn as the curves they are on the fisheye, plus the edges of
    "ahead" (AHEAD_DEG either side of straight on, for the detector and the depth sense alike);
  - the target box find is walking up to, and what it is looking for.

STOP_BOTTOM can be changed from the page (set_stop_bottom), between STOP_BOTTOM_MIN and
STOP_BOTTOM_MAX. It changes where find's walk-up counts as arrived and where the depth sense's "right at my
feet" starts, until the server restarts; to keep a new value, change STOP_BOTTOM in fisheye_geom.py.
Nothing here moves the robot.
"""

from __future__ import annotations

import math
import time

import fisheye_geom as geom
import blind_mode as walker
import vision
from robot_api import Result

STOP_BOTTOM_DEFAULT = geom.STOP_BOTTOM  # 454: Lab 5's, measured on the robot
STOP_BOTTOM_MIN = 380  # higher in the image = stops farther away
STOP_BOTTOM_MAX = 520  # lower = closer; past this the thing's base is under the robot's nose
STRIP_BEARINGS = {"left": (-80, -45), "center-left": (-45, -15), "center": (-15, 15), "center-right": (15, 45), "right": (45, 80)}


# ============================================================ what the robot does
def set_stop_bottom(px: float) -> Result:
    """Move Lab 5's stop row (arrival) to ``px`` of 572, until the server restarts."""
    try:
        px = float(px)
    except (TypeError, ValueError):
        return Result(False, f"stop_bottom must be a number of pixels, {STOP_BOTTOM_MIN}-{STOP_BOTTOM_MAX}")
    if not STOP_BOTTOM_MIN <= px <= STOP_BOTTOM_MAX:
        return Result(False, f"stop_bottom {px:g} is out of range: {STOP_BOTTOM_MIN}-{STOP_BOTTOM_MAX} px of"
                             f" {geom.IMAGE_HEIGHT} (Lab 5's is {STOP_BOTTOM_DEFAULT})")
    px = int(round(px))
    geom.STOP_BOTTOM = px
    geom.STOP_NEARNESS = px / geom.IMAGE_HEIGHT
    geom.SLOW_NEARNESS = (px - geom.SLOWDOWN_PX) / geom.IMAGE_HEIGHT
    walker.ARRIVE_NEARNESS = geom.STOP_NEARNESS  # blind_mode copied these when it was imported
    walker.SLOW_NEARNESS = geom.SLOW_NEARNESS
    return Result(True, f"stop_bottom is {px} px of {geom.IMAGE_HEIGHT} (nearness {geom.STOP_NEARNESS:.3f}) until the"
                        f" server restarts; Lab 5's is {STOP_BOTTOM_DEFAULT}")


def project(lat_deg: float, lon_deg: float) -> list[float] | None:
    """Where a direction (latitude + down, longitude + right) lands in the raw frame, as fractions:
    Lab 5's double sphere model forward (fisheye_geom has the inverse)."""
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    x, y, z = math.cos(lat) * math.sin(lon), math.sin(lat), math.cos(lat) * math.cos(lon)
    d1 = math.sqrt(x * x + y * y + z * z)
    k2 = geom.XI * d1 + z
    den = geom.ALPHA * math.sqrt(x * x + y * y + k2 * k2) + (1 - geom.ALPHA) * k2
    if den <= 1e-9:
        return None
    u, v = (geom.FX * x / den + geom.CX) / geom.FRAME_W, (geom.FY * y / den + geom.CY) / geom.FRAME_H
    return [round(u, 4), round(v, 4)] if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 else None


def row_curve(nearness: float, lon_from: float = -88.0, lon_to: float = 88.0, step: float = 4.0) -> list[list[float]]:
    """A row of Lab 5's undistorted image (a nearness) as the curve it is on the fisheye."""
    lat = nearness * 180.0 - 90.0
    pts, lon = [], lon_from
    while lon <= lon_to + 1e-9:
        p = project(lat, lon)
        if p:
            pts.append(p)
        lon += step
    return pts


def bearing_line(lon: float) -> list[list[float]]:
    """A bearing (degrees right of straight on) as the curve it is on the fisheye, floor to sky."""
    return [p for lat in range(-60, 90, 4) for p in [project(lat, lon)] if p]


def _detection(d: dict) -> dict:
    near = geom.box_nearness(d["box"])
    close = vision.is_close(d)
    ahead = vision.in_ahead(d)
    counted = vision.counts(d)
    lens = vision.in_lens(d["box"])
    why_not = ("" if counted else f"score under {vision.MIN_SCORE}" if d["score"] < vision.MIN_SCORE
               else f"only {lens:.0%} inside the lens (needs {vision.MIN_IN_LENS:.0%}): the detector seeing the border")
    return {"label": d["label"], "score": round(d["score"], 2), "box": d["box"], "bearing": round(geom.box_bearing_deg(d["box"])),
            "in_lens": None if lens is None else round(lens, 2), "ignored_because": why_not,
            "nearness": None if near is None else round(near, 3), "how_close": vision.how_close(d), "close": close,
            "ahead": ahead, "counted": counted, "blocks_forward": counted and close and ahead}


def debug_state(robot=None) -> dict:
    """Everything the debug page draws, in one reply. Keeps the eyes awake while the page is open."""
    vision.wake(3.0)
    now = time.time()
    dets = [_detection(d) for d in vision.VISION["detections"]] if now - vision.VISION["time"] <= vision.FRESH_S else []
    fresh = vision.fresh_freespace()
    strips = [{**st, "bearings": STRIP_BEARINGS.get(st["name"]), "blocked": vision.strip_blocked(st),
               "curve": row_curve(st["nearness"], *STRIP_BEARINGS[st["name"]], 2.0)
               if st.get("nearness") is not None and st["name"] in STRIP_BEARINGS else []}
              for st in (fresh or [])]
    try:
        walk = robot.speeds().walk if robot is not None else 0.3
    except Exception:
        walk = 0.3
    target = walker.TARGET
    return {
        "time": now,
        "verdict": {"forward": vision.path_blocked(walk, 0.0), "by_detector": vision.detection_blocked(walk, 0.0),
                    "by_depth": vision.freespace_blocked(walk, 0.0)},
        "detections": dets,
        "detections_age_s": round(now - vision.VISION["time"], 1) if vision.VISION["time"] else None,
        "min_score": vision.MIN_SCORE,
        "min_in_lens": vision.MIN_IN_LENS,
        "circle": vision.FREESPACE.get("circle"),
        "strips": strips,
        "depth_age_s": round(now - vision.FREESPACE["time"], 1) if vision.FREESPACE["time"] else None,
        "depth_source": vision.FREESPACE.get("source", ""),
        "wanted": walker.WANTED["name"],
        "target": {"name": target["name"], "box": target["box"], "source": target["source"],
                   "age_s": round(now - target["time"], 1) if target["time"] else None},
        "tuning": {"stop_bottom": geom.STOP_BOTTOM, "stop_bottom_default": STOP_BOTTOM_DEFAULT,
                   "stop_bottom_min": STOP_BOTTOM_MIN, "stop_bottom_max": STOP_BOTTOM_MAX,
                   "stop_nearness": round(geom.STOP_NEARNESS, 3), "block_nearness": vision.BLOCK_NEARNESS},
        "lines": {"stop": row_curve(geom.STOP_NEARNESS), "block": row_curve(vision.BLOCK_NEARNESS),
                  "slow": row_curve(geom.SLOW_NEARNESS),
                  "ahead": [bearing_line(-vision.AHEAD_DEG), bearing_line(vision.AHEAD_DEG)], "ahead_deg": vision.AHEAD_DEG},
    }
