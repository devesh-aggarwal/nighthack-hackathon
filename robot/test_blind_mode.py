#!/usr/bin/env python3
"""Desk tests for blind mode (lab/blind_mode.py, lab/vision.py, lab/debug_view.py). No robot, no
ROS, no Gemini, no camera, no speaker.

    python3 lab/test.py blind    # just these
    python3 lab/test.py          # the lab's checks, deaf mode's, then these

They run on test_tools.py's fake robot and fake clock. The eyes service is played by the tests:
a kinematic stand-in for the camera (World: one thing at a bearing and a distance that every speed
command moves) and a kinematic room (Room: walls and things, the depth strips by ray casting).
"""

from __future__ import annotations

import asyncio
import math
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_tools as tt  # noqa: E402  a fake ROS, and lab/ loaded
from test_tools import LOCOMOTION_CONTROLLER, Rig, check, tools  # noqa: E402

import blind_mode as blind  # noqa: E402
import deaf_mode as deaf  # noqa: E402
import debug_view as debug  # noqa: E402
import fisheye_geom  # noqa: E402
import vision  # noqa: E402


def no_eyes(url, path, payload, timeout=0):
    return {"found": False, "source": "gemma"}


async def _fake_beep() -> bool:
    return True


class Wall:
    """The lab's time.time() on the rig's fake clock, so boxes and depth summaries age as the robot's time
    passes (with the real clock nothing posted ever went stale, and a box gone for good still steered)."""

    def __init__(self, clock) -> None:
        self.clock, self.base = clock, time.time() - clock.now

    def time(self) -> float:
        return self.base + self.clock.now

    def __getattr__(self, name):
        return getattr(time, name)


def make_rig() -> Rig:
    rig = Rig()
    blind.time = vision.time = debug.time = Wall(rig.clock)
    return rig


def reset() -> None:
    vision.VISION.update(time=0.0, detections=[])
    vision.FREESPACE.update(time=0.0, strips=[], mode="", eyes_url="", circle=list(vision.WHOLE_FRAME))
    vision.ask_eyes = no_eyes  # no eyes service here
    blind.TARGET.update(time=0.0, name="", box=None)
    blind.WANTED.update(name="", since=0.0)
    blind.EXPLORING.update(name="", since=0.0)
    blind.stop_beacon()
    blind.PLAY, blind.CAN_PLAY = _fake_beep, (lambda: True)  # no speaker on a desk
    deaf.OWN_SOUND_UNTIL = 0.0


# ----------------------------------------------------------------------------- the fake camera
def project(x: float, y: float, z: float) -> tuple[float, float]:
    """Lab 5's forward lens model (follow_me_pupper/robot/fisheye_converter.py): a ray -> frame fractions."""
    g = fisheye_geom
    r2 = x * x + y * y
    d1 = math.sqrt(r2 + z * z)
    k2 = g.XI * d1 + z
    d2 = math.sqrt(r2 + k2 * k2)
    den = g.ALPHA * d2 + (1 - g.ALPHA) * k2
    return (g.FX * x / den + g.CX) / g.FRAME_W, (g.FY * y / den + g.CY) / g.FRAME_H


def frame_point(bearing_deg: float, lat_deg: float) -> tuple[float, float]:
    """Where a thing at bearing (+ right) and latitude (+ down) appears, as frame fractions."""
    lat, lon = math.radians(lat_deg), math.radians(bearing_deg)
    return project(math.cos(lat) * math.sin(lon), math.sin(lat), math.cos(lat) * math.cos(lon))


def floor_lat(distance_m: float, cam_height_m: float = 0.22) -> float:
    """The latitude of a point on the floor distance_m ahead, for a level camera cam_height_m up."""
    return math.degrees(math.atan2(cam_height_m, max(distance_m, 0.02)))


def row_of(distance_m: float) -> float:
    """Lab 5's row (nearness) of a floor point distance_m ahead of a level camera 0.22 m up."""
    return (floor_lat(distance_m) + 90.0) / 180.0


def box(cx: float, w: float, h: float, bottom: float = 1.0) -> list[float]:
    """A detection box from its center x, width, height and bottom edge (fractions of the frame)."""
    return [cx - w / 2, bottom - h, w, h]


def see(*dets: tuple[str, list[float]]) -> None:
    vision.update_detections([{"label": label, "score": 0.9, "box": b} for label, b in dets], 640, 480)


def depth(**per_strip) -> None:
    """Post a free-space summary: depth(center=0.3) in metres, depth(center=("near", 0.6)) relative,
    or depth(center=("row", 0.7)) as Lab 5's row, as the eyes service posts it."""
    strips, mode = [], "metric"
    for name in vision.STRIPS:
        v = per_strip.get(name.replace("-", "_"))
        if isinstance(v, tuple) and v[0] == "row":
            strips.append({"name": name, "closest_m": None, "near_frac": None, "nearness": v[1]})
        elif isinstance(v, tuple):
            mode = "relative"
            strips.append({"name": name, "closest_m": None, "near_frac": v[1]})
        else:
            strips.append({"name": name, "closest_m": 2.0 if v is None else v, "near_frac": 0.05 if v is None else 0.9})
    vision.update_freespace(strips, mode, "http://eyes.test:8100")


async def call(rig: Rig, tool: str, **args):
    """Call a tool the way Gemini does (Rig.call can't take a parameter called name)."""
    res = await tools.call(rig.ts, tool, args)
    await rig.settle()
    return res


async def walk_up(rig: Rig, name: str, beacon: bool = False):
    res = await blind.walk_up(rig.ts, name, beacon=beacon)
    await rig.settle()
    return res


def turns(rig: Rig) -> list:
    return [m for m in tt.walk_twists(rig) if abs(m.angular.z) > 1e-9]


def events_since(before: int) -> list[dict]:
    return deaf.EVENTS.since(before)


class World:
    """A kinematic stand-in for the camera: one thing at a bearing and a distance from Pupper.
    Every speed Pupper sends moves it; every tick the eyes service 'sees' it as a box."""

    H_AT_1M = 0.2  # box height (fraction of the frame) when the thing is 1 m away

    def __init__(self, rig: Rig, name: str, bearing_deg: float, distance_m: float, see: bool = True, every: int = 1,
                 delay: int = 0, blur: bool = False):
        self.rig, self.name, self.bearing, self.distance, self.see = rig, name, bearing_deg, distance_m, see
        self.every = every  # a fix only every this many ticks (Gemma is slow); the depth strip updates every tick
        self.delay = delay  # a fix arrives this many ticks after its frame was taken (the Pi's detector, Gemma's answer)
        self.frames: list = []  # (clock, box) taken but not yet posted
        self.clock = 0  # every speed command sent, moving or not: time passing, for the delay
        self.blur = blur  # Gemma: a frame taken mid-turn gives no box (blurred, and it wouldn't agree with the last)
        self.spinning = False
        blind.TARGET.update(time=0.0, name="", box=None)  # no box left over from the last world (same name, still fresh)
        self.ticks, self.turn_ticks, self.walk_ticks, self.first_walk_bearing = 0, 0, 0, None
        self.on_tick = None
        self.obstacle: dict = {}  # something else the depth sense sees, e.g. {"center": 0.25}: a cable on the route
        self._publish = rig.robot.publish
        rig.robot.publish = self.publish
        self.update()

    def box(self) -> list[float]:
        h = min(0.95, self.H_AT_1M / max(self.distance, 0.05))
        u, v = frame_point(self.bearing, floor_lat(self.distance))
        return box(u, h * 0.6, h, min(0.99, v))

    def update(self) -> None:
        if self.see and self.clock % self.every == 0 and not (self.blur and self.spinning):  # the eyes look by the clock
            if self.delay:
                if not self.frames or self.frames[-1][0] != self.clock:
                    self.frames.append((self.clock, self.box()))
            else:
                blind.update_target(self.name, self.box(), 0.8, "eyes")
        while self.frames and self.clock - self.frames[0][0] >= self.delay:  # the answer about an old frame arrives
            taken, b = self.frames.pop(0)
            blind.update_target(self.name, b, 0.8, "eyes", frame_age_s=(self.clock - taken) / tt.LLM_CMD_RATE)
        if self.see:  # the depth sense sees the thing's base in its strip (and floor elsewhere), as a Lab 5 row
            strip = blind.strip_at(self.bearing).replace("-", "_")
            depth(**{strip: ("row", round(row_of(self.distance), 3)), **self.obstacle})

    def publish(self, topic, msg):
        self._publish(topic, msg)
        if topic != "/llm_cmd_vel":
            return
        dt = 1.0 / tt.LLM_CMD_RATE
        vx, wz = msg.linear.x, math.degrees(msg.angular.z)
        self.clock += 1
        self.spinning = abs(wz) > 1e-9
        if abs(vx) + abs(wz) > 1e-9:
            self.ticks += 1
            if abs(wz) > 1e-9:
                self.turn_ticks += 1
            if vx > 1e-9:
                self.walk_ticks += 1
                if self.first_walk_bearing is None:
                    self.first_walk_bearing = self.bearing
            self.bearing += wz * dt  # a left turn (wz > 0) swings a thing on the left (bearing < 0) toward ahead
            self.distance -= vx * math.cos(math.radians(self.bearing)) * dt
        if self.on_tick:
            self.on_tick(self)
        self.update()

    def close(self) -> None:
        self.rig.robot.publish = self._publish


class Room:
    """A kinematic room for find: four walls, things standing in it, and the senses computed from the
    pose every tick (the depth strips by ray casting, the detector when a thing is in view). Every
    speed Pupper sends moves it; the room records how close it came to a wall."""

    EDGES = [-80.0, -45.0, -15.0, 15.0, 45.0, 80.0]  # the strips' bearings, as the eyes service cuts them
    SEE_M = 2.5  # the detector sees a thing within this range and 80 degrees of straight on
    THING_R = 0.05

    def __init__(self, rig: Rig, w: float, h: float, x: float, y: float, heading: float, things: dict, coco: bool = True):
        self.rig, self.w, self.h, self.x, self.y, self.heading = rig, w, h, x, y, heading
        self.things, self.coco = things, coco  # name -> (x, y); coco: the detector knows them (else only Gemma)
        self.min_wall = min(x, w - x, y, h - y)
        self.cells: set = set()
        self.ticks = self.walk_ticks = 0
        self._publish = rig.robot.publish
        rig.robot.publish = self.publish
        self.sense()

    def ray(self, bearing: float) -> float:
        a = math.radians(self.heading - bearing)
        dx, dy = math.cos(a), math.sin(a)
        ts = []
        for t in ((-self.x / dx) if dx else None, ((self.w - self.x) / dx) if dx else None,
                  (-self.y / dy) if dy else None, ((self.h - self.y) / dy) if dy else None):
            if t is not None and t > 0:
                ts.append(t)
        for tx, ty in self.things.values():
            fx, fy = self.x - tx, self.y - ty
            b = 2 * (fx * dx + fy * dy)
            c = fx * fx + fy * fy - self.THING_R ** 2
            disc = b * b - 4 * c
            if disc >= 0:
                t = (-b - math.sqrt(disc)) / 2
                if t > 0:
                    ts.append(t)
        return min(ts) if ts else 9.0

    def strips(self) -> dict:
        out = {}
        for name, b0, b1 in zip(vision.STRIPS, self.EDGES[:-1], self.EDGES[1:]):
            r = min(self.ray(b0 + (b1 - b0) * k / 4) for k in range(5))
            out[name.replace("-", "_")] = ("row", round(row_of(r), 3))
        return out

    def bearing_to(self, name: str) -> tuple[float, float]:
        tx, ty = self.things[name]
        d = math.hypot(tx - self.x, ty - self.y)
        b = (self.heading - math.degrees(math.atan2(ty - self.y, tx - self.x)) + 180.0) % 360.0 - 180.0
        return b, d

    def visible(self, name: str) -> bool:
        b, d = self.bearing_to(name)
        return d <= self.SEE_M and abs(b) <= 80.0

    def sense(self) -> None:
        depth(**self.strips())
        dets = []
        for name in self.things:
            if self.coco and self.visible(name):
                b, d = self.bearing_to(name)
                hgt = min(0.95, 0.2 / max(d, 0.05))
                u, v = frame_point(b, floor_lat(d))
                dets.append((name, box(u, hgt * 0.6, hgt, min(0.99, v))))
        if dets:
            see(*dets)
        else:
            vision.VISION.update(time=0.0, detections=[])

    def publish(self, topic, msg):
        self._publish(topic, msg)
        if topic != "/llm_cmd_vel":
            return
        dt = 1.0 / tt.LLM_CMD_RATE
        vx, wz = msg.linear.x, math.degrees(msg.angular.z)
        self.ticks += 1
        if vx > 1e-9:
            self.walk_ticks += 1
        self.heading += wz * dt
        self.x += vx * math.cos(math.radians(self.heading)) * dt
        self.y += vx * math.sin(math.radians(self.heading)) * dt
        self.min_wall = min(self.min_wall, self.x, self.w - self.x, self.y, self.h - self.y)
        self.cells.add((int(self.x), int(self.y)))
        self.sense()

    def close(self) -> None:
        self.rig.robot.publish = self._publish


# ----------------------------------------------------------------------------- the senses
async def vision_tests() -> None:
    print("Blind mode A: the senses and the obstacle rule (lab/vision.py)")
    reset()
    rig = make_rig()
    n = vision.update_detections([{"label": "chair", "score": 0.8, "box": [0.3, 0.4, 0.4, 0.6]}, {"bad": 1}, {"label": "x", "box": [0, 0, 0, 0]}])
    check("update_detections keeps the good entries and drops the bad", n == 1, str(n))
    vision.VISION["time"] = 0.0
    check("stale detections are no eyes: path_blocked is None", vision.path_blocked(0.4, 0.0) is None)
    see(("chair", box(0.5, 0.4, 0.6)))
    why = vision.path_blocked(0.4, 0.0) or ""
    check("a close chair straight ahead blocks walking forward, naming it", "chair" in why and "in front" in why, why)
    check("... but not backing away or turning in place", vision.path_blocked(-0.3, 0.0) is None and vision.path_blocked(0.0, 0.0) is None)
    see(("person", box(0.15, 0.25, 0.5)))
    check("a close person on the left blocks a sidestep left", "person" in (vision.path_blocked(0.0, 0.3) or ""))
    check("... but not a sidestep right, nor walking forward", vision.path_blocked(0.0, -0.3) is None and vision.path_blocked(0.4, 0.0) is None)
    u, _ = frame_point(70.0, 20.0)  # someone standing right beside the robot, 70 degrees to its right
    beside = box(u, 0.12, 0.55, bottom=0.97)
    see(("person", beside))
    d = {"label": "person", "score": 0.9, "box": beside}
    check("a person beside the robot (70 degrees off) is close but not ahead", vision.is_close(d) and not vision.in_ahead(d),
          f"bearing {fisheye_geom.box_bearing_deg(beside):.0f}, close {vision.is_close(d)}")
    check("... so it doesn't block walking forward (a 0.25-0.75 band of the width spans about +-90 degrees)",
          vision.path_blocked(0.4, 0.0) is None)
    see(("cup", box(0.5, 0.1, 0.1, 0.5)))
    check("a small far cup does not block", vision.path_blocked(0.4, 0.0) is None)

    # The detector seeing the lens's black border as a huge person (measured on the robot's debug view).
    vision.FREESPACE["circle"] = [0.496, 0.497, 0.283, 0.38]  # the image disc, as the eyes service measures it
    ghost = [0.163, 0.046, 0.814, 0.943]  # "person 0.96": reaching far out into the black around the disc
    see(("person", ghost))
    check("a huge 'person' mostly outside the lens circle doesn't count", not vision.counts(vision.VISION["detections"][0])
          and vision.path_blocked(0.4, 0.0) is None, f"in lens {vision.in_lens(ghost)}")
    see(("person", [0.213, 0.117, 0.566, 0.76]))  # a real person right in front, filling the disc
    check("a real person filling the view still blocks", "person" in (vision.path_blocked(0.4, 0.0) or ""))
    vision.FREESPACE["circle"] = list(vision.WHOLE_FRAME)
    check("without a measured circle (no eyes service) nothing is filtered",
          vision.counts({"label": "person", "score": 0.96, "box": ghost}))
    vision.VISION.update(time=0.0, detections=[])

    n = vision.update_freespace([{"name": "center", "closest_m": 0.3}, {"name": "nope", "closest_m": 0.1}, {"bad": 1}], "metric")
    check("update_freespace keeps the good strips and drops the bad", n == 1, str(n))
    vision.FREESPACE["time"] = 0.0
    check("a stale depth summary is no sense", vision.path_blocked(0.4, 0.0) is None)
    vision.update_freespace([{"name": "center", "closest_m": 0.3}], "metric", frame_age_s=vision.FREESPACE_FRESH_S + 1)
    check("a post from an old frame counts as that old", vision.path_blocked(0.4, 0.0) is None)
    depth(center=0.3)
    why = vision.path_blocked(0.4, 0.0) or ""
    check("something 0.3 m ahead blocks, without naming it", "in front" in why and "depth sense" in why, why)
    depth(center=0.8)
    check("0.8 m does not", vision.path_blocked(0.4, 0.0) is None)
    depth(center_left=("near", 0.6))
    check("relative mode: a near surface ahead-left blocks", "ahead on my left" in (vision.path_blocked(0.4, 0.0) or ""))
    depth(center=("row", 0.72))
    check("Lab 5's row: a base at 0.72 (past the 0.68 block row) blocks", "very close" in (vision.path_blocked(0.4, 0.0) or ""))
    depth(center=("row", 0.6))
    check("... 0.6 does not", vision.path_blocked(0.4, 0.0) is None)
    depth(center=0.2)
    see(("chair", box(0.5, 0.4, 0.6)))
    why = vision.path_blocked(0.4, 0.0) or ""
    check("when the detector can name it, it is named (chair), not 'something'", "chair" in why and "something low" not in why, why)
    vision.VISION.update(time=0.0, detections=[])
    res = await call(rig, "move", direction="forward", duration=1.0)
    check("the lab's own move is unchanged: no obstacle rule outside find", res.ok, res.message)
    rig.close()


# ----------------------------------------------------------------------------- walking up to a thing
async def walk_up_tests() -> None:
    print("Blind mode B: walking up to a thing (walk_up, the end of find)")
    reset()
    rig = make_rig()
    depth()
    world = World(rig, "red mug", bearing_deg=-25.0, distance_m=2.0)
    before, start, t0 = deaf.EVENTS.last_id, len(rig.records), rig.clock.now
    res = await walk_up(rig, "red mug")
    world.close()
    check("a mug 25 degrees to the left: accepted, says what it sees and will do", res.ok and "to the left" in res.message
          and "queued" in res.message and "arm's length" in res.message, res.message)
    outcomes = rig.outcomes(start)
    check("... the job reaches it", any("reached the red mug" in o for o in outcomes), str(outcomes))
    check("... turning left first, before walking", world.first_walk_bearing is not None and abs(world.first_walk_bearing) <= blind.WALK_WITHIN_DEG
          and world.turn_ticks > 0, f"first walk at bearing {world.first_walk_bearing}, turn ticks {world.turn_ticks}")
    check("... ends at arm's length (under 0.65 m), facing it (under 10 degrees)", world.distance < 0.65 and abs(world.bearing) < 10,
          f"distance {world.distance:.2f} m, bearing {world.bearing:.1f} deg")
    check("... in well under the 20 s limit", rig.clock.now - t0 < 15, f"{rig.clock.now - t0:.1f} s")
    check("... never faster than the walk and turn speeds", all(m.linear.x <= 0.4 + 1e-9 and abs(math.degrees(m.angular.z)) <= 60 + 1e-6
                                                               for m in tt.walk_twists(rig)))
    evs = events_since(before)
    check("... Gemini hears the result, and the screen shows it in green", any(e["kind"] == "goto" and "reached" in e["for_gemini"]
          and e["display"] and e["display"]["text"] == "RED MUG" and e["display"]["color"] == blind.GREEN for e in evs), str(evs))
    check("... and the eyes service is no longer asked for it, ending in locomotion", blind.WANTED["name"] == ""
          and rig.robot.controllers[LOCOMOTION_CONTROLLER] == "active")

    world = World(rig, "ball", bearing_deg=0.0, distance_m=1.0)
    start = len(rig.records)
    await walk_up(rig, "ball")
    world.close()
    check("a ball straight ahead: reached without turning", any("reached the ball" in o for o in rig.outcomes(start)) and world.turn_ticks == 0,
          f"turn ticks {world.turn_ticks}; {rig.outcomes(start)}")

    world = World(rig, "box", bearing_deg=0.0, distance_m=0.3)
    start = len(rig.records)
    await walk_up(rig, "box")
    world.close()
    depth()
    check("a thing whose base is already at the blocking row (0.3 m) is reached without walking",
          any("reached the box" in o for o in rig.outcomes(start)) and world.walk_ticks == 0, f"walk ticks {world.walk_ticks}; {rig.outcomes(start)}")

    world = World(rig, "cone", bearing_deg=0.0, distance_m=1.0)
    world.see = False
    blind.update_target("cone", box(0.5, 0.03, 0.03, 0.99), 0.8, "eyes", bottom_known=False)  # a model that only pointed
    depth(center=0.4)
    start = len(rig.records)
    await walk_up(rig, "cone")
    world.close()
    depth()
    check("a point without a real bottom edge: dead ahead and blocked at arm's length counts (stopped in front)",
          any("reached the cone: stopped right in front of it" in o for o in rig.outcomes(start)), str(rig.outcomes(start)))

    world = World(rig, "wagon", bearing_deg=-25.0, distance_m=2.0)
    world.see = False
    blind.update_target("wagon", box(0.5 - 25 / blind.HFOV_DEG, 0.03, 0.03, 0.5), 0.8, "eyes", bottom_known=False)
    world.obstacle = {"center_left": 0.5}
    depth(center_left=0.5)
    start = len(rig.records)
    await walk_up(rig, "wagon")
    world.close()
    depth()
    check("a pointed-at thing with a chair base in its direction is not 'reached' at 0.5 m",
          not any("reached the wagon" in o and "about 0.5 m" in o for o in rig.outcomes(start)), str(rig.outcomes(start)))

    world = World(rig, "cup", bearing_deg=-30.0, distance_m=1.5, every=40)  # a fix only every 2 s, as with Gemma
    start = len(rig.records)
    await walk_up(rig, "cup")
    world.close()
    depth()
    check("with a fix only every 2 s it still gets there, dead-reckoning the aim between fixes",
          any("reached the cup" in o for o in rig.outcomes(start)) and world.distance < 0.65 and abs(world.bearing) < 12,
          f"distance {world.distance:.2f} m, bearing {world.bearing:.1f} deg; {rig.outcomes(start)}")

    # Seen on the robot: each box arrives 2 s after its frame was taken, so steering on it as if it were
    # current turns past the thing and then the wrong way. The robot takes off what it turned since the frame.
    for bearing in (-50.0, 40.0):
        world = World(rig, "water bottle", bearing_deg=bearing, distance_m=1.8, every=20, delay=40)
        start = len(rig.records)
        await walk_up(rig, "water bottle")
        world.close()
        depth()
        check(f"a fix 2 s old on arrival, a bottle {bearing:+.0f} degrees off: it still turns the right way and gets there",
              any("reached the water bottle" in o for o in rig.outcomes(start)) and world.distance < 0.65 and abs(world.bearing) < 12,
              f"distance {world.distance:.2f} m, bearing {world.bearing:.1f} deg; {rig.outcomes(start)}")

    # A Gemma-only target (a blue wagon: a box every 1.5 s, 1.5 s late, none from a frame taken mid-turn): turn,
    # then stand still and look, and it gets there in one go. (On the robot the wagon was "lost" again and again
    # because the eyes service cleared the box whenever two answers disagreed or a request failed; that is
    # vision/eyes.py's TargetTracker, which this desk test doesn't run.)
    world = World(rig, "blue wagon", bearing_deg=65.0, distance_m=2.0, every=30, delay=30, blur=True)
    start = len(rig.records)
    await walk_up(rig, "blue wagon")
    world.close()
    depth()
    check("a Gemma-only wagon 65 degrees off (slow, late boxes, none mid-turn): reached without losing it",
          any("reached the blue wagon" in o for o in rig.outcomes(start)) and world.distance < 0.65 and abs(world.bearing) < 12,
          f"distance {world.distance:.2f} m, bearing {world.bearing:.1f} deg; {rig.outcomes(start)}")

    # Seen on the robot: with Gemma on the Pi the first box took several seconds, and the walk-up's 18 s
    # (move's 7 m at walking speed) ran out while it stood and looked: it walked 2 s and gave up with the wagon
    # still ahead. Only time spent moving counts against it now.
    world = World(rig, "blue wagon", bearing_deg=0.0, distance_m=3.5, see=False)

    def slow_first_box(w: World):
        if w.clock == 240:  # Gemma's first box after 12 s of standing and looking
            w.see = True

    world.on_tick = slow_first_box
    start = len(rig.records)
    await walk_up(rig, "blue wagon")
    world.close()
    depth()
    check("a first box after 12 s of standing still: the waiting doesn't use up the walk's time, and it gets there",
          any("reached the blue wagon" in o for o in rig.outcomes(start)) and world.distance < 0.65,
          f"distance {world.distance:.2f} m; {rig.outcomes(start)}")

    # Seen on the robot: it walked right up to the wagon, then stood waiting for a second box to confirm it was
    # there, and Gemma, with the wagon filling the view at the edge of the lens, gave none: "lost sight" at the
    # finish. Losing sight once it is close is arriving: it is too close for the eyes.
    world = World(rig, "blue wagon", bearing_deg=-10.0, distance_m=2.0, every=30, delay=30)

    def too_close_to_see(w: World):
        if w.distance < 0.8:
            w.see = False
            w.frames.clear()

    world.on_tick = too_close_to_see
    start = len(rig.records)
    await walk_up(rig, "blue wagon")
    world.close()
    depth()
    outcomes = rig.outcomes(start)
    check("sight lost once it is close: counted as arrived (too close to see), not 'lost'",
          any("reached the blue wagon" in o and "too close" in o for o in outcomes) and not any("lost sight" in o for o in outcomes),
          f"distance {world.distance:.2f} m; {outcomes}")

    # The robot's own cadence (log, 30 Oct): Gemma boxes 4-5 s apart, each about 2 s old. Seeing the wagon all the
    # way in, it must arrive by the stop row, not stop early with "too close to see" during an ordinary gap.
    world = World(rig, "blue wagon", bearing_deg=-10.0, distance_m=2.5, every=100, delay=40)
    start = len(rig.records)
    await walk_up(rig, "blue wagon")
    world.close()
    depth()
    outcomes = rig.outcomes(start)
    check("a box every 5 s, 2 s late, seen all the way in: arrives at the stop row, not 'too close' a metre out",
          any("reached the blue wagon" in o for o in outcomes) and not any("too close" in o for o in outcomes)
          and world.distance < 0.65, f"distance {world.distance:.2f} m; {outcomes}")
    blind.stop_beacon()

    # Sight lost while it is still far: not an arrival, and the robot looks again by itself instead of asking.
    world = World(rig, "blue wagon", bearing_deg=0.0, distance_m=3.5, every=20)

    def gone_far(w: World):
        if w.distance < 2.6:
            w.see = False

    world.on_tick = gone_far
    before, start = deaf.EVENTS.last_id, len(rig.records)
    await walk_up(rig, "blue wagon")
    world.close()
    depth()
    outcomes = rig.outcomes(start)
    evs = events_since(before)
    check("sight lost while far: not 'reached', and it looks again by itself", not any("reached the blue wagon" in o for o in outcomes)
          and any("find blue wagon" in o for o in outcomes), str(outcomes))
    check("... and Gemini is told it is looking again, not to ask the person", any("looking again" in e["for_gemini"]
          and "don't ask" in e["for_gemini"] for e in evs) and not any("Ask me to find it again" in e["for_gemini"] for e in evs),
          str([e["for_gemini"][:100] for e in evs]))
    blind.stop_beacon()

    world = World(rig, "person", bearing_deg=15.0, distance_m=3.0)

    def vanish(w: World):
        if w.ticks == 20:
            w.see = False
            blind.TARGET["time"] = 0.0

    world.on_tick = vanish
    start = len(rig.records)
    await walk_up(rig, "person")
    world.close()
    check("losing sight of it stops the walk and says so", any("lost sight of the person" in o for o in rig.outcomes(start)), str(rig.outcomes(start)))

    before, start = deaf.EVENTS.last_id, len(rig.records)
    res = await walk_up(rig, "unicorn")
    check("a thing the eyes never find: accepted with 'once my eyes find it'", res.ok and "once my eyes find it" in res.message, res.message)
    check("... after waiting it looks again by itself, and tells Gemini not to ask the person",
          any("couldn't see the unicorn" in o and "looking again by myself" in o for o in rig.outcomes(start))
          and any(o.startswith("find unicorn") for o in rig.outcomes(start))
          and any("don't ask" in e["for_gemini"] for e in events_since(before)), str(rig.outcomes(start)))

    world = World(rig, "door", bearing_deg=0.0, distance_m=3.0)

    def cable(w: World):
        if w.ticks == 20:
            w.obstacle = {"center": 0.25}  # a cable on the floor, not the door (3 m off)

    world.on_tick = cable
    start = len(rig.records)
    await walk_up(rig, "door")
    world.close()
    depth()
    check("something low in the way stops it, with the depth sense's reason", any("stopped on the way to the door" in o
          and "depth sense" in o for o in rig.outcomes(start)), str(rig.outcomes(start)))

    world = World(rig, "couch", bearing_deg=0.0, distance_m=0.9)

    def arrive(w: World):
        if w.distance < 0.6:
            depth(center=0.3)  # the couch itself fills the depth sense

    world.on_tick = arrive
    start = len(rig.records)
    await walk_up(rig, "couch")
    world.close()
    depth()
    check("the target itself blocking the way counts as arriving", any("reached the couch" in o for o in rig.outcomes(start)), str(rig.outcomes(start)))

    see(("cup", box(0.7, 0.2, 0.3, 0.9)))  # the COCO detector sees it; the eyes service has nothing
    res = await walk_up(rig, "mug")
    check("a thing the COCO detector knows is used when the eyes service has no fix (mug = cup)",
          res.ok and "(detector)" in res.message and "degrees" in res.message, res.message)
    vision.VISION.update(time=0.0, detections=[])

    await call(rig, "follow", enabled=True)
    world = World(rig, "chair", bearing_deg=0.0, distance_m=1.0)
    res = await walk_up(rig, "chair")
    world.close()
    check("walking up ends follow mode and says so", res.ok and "follow" in res.message.lower() and not rig.ts.following, res.message)
    rig.close()


# ----------------------------------------------------------------------------- find
async def find_tests() -> None:
    print("Blind mode C: find (look, sweep, roam, walk up to it, beep)")
    reset()
    rig = make_rig()
    res = await call(rig, "find", what="")
    check("find with nothing is refused with an example", not res.ok and "e.g." in res.message, res.message)
    res = await call(rig, "find", what="a chair", max_seconds=1000)
    check("a search over the time limit is refused with the limit", not res.ok and "300" in res.message, res.message)

    depth()
    see(("cup", box(0.8, 0.2, 0.3, 0.9)))
    before, start = deaf.EVENTS.last_id, len(rig.records)
    res = await call(rig, "find", what="my mug")
    vision.VISION.update(time=0.0, detections=[])
    check("a mug the detector sees: accepted, says it looks first and will beep", res.ok and "I look" in res.message
          and "beep" in res.message and "queued" in res.message, res.message)
    check("... found right away, to the right", any("found the mug right away" in o and "+" in o for o in rig.outcomes(start)),
          str(rig.outcomes(start)))
    check("... and the screen shows it in green", any(e["kind"] == "found" and e["display"]["text"] == "MUG" for e in events_since(before)))
    blind.stop_beacon()

    reset()
    depth()
    spins_seen = [0]
    publish = rig.robot.publish

    def publish_and_find(topic, msg):
        publish(topic, msg)
        if topic == "/llm_cmd_vel" and abs(msg.angular.z) > 0:
            spins_seen[0] += 1
            if spins_seen[0] == 40:  # during the second step: the ball comes into view
                see(("sports ball", box(0.5, 0.2, 0.25, 0.95)))

    rig.robot.publish = publish_and_find
    before, start = deaf.EVENTS.last_id, len(rig.records)
    await call(rig, "find", what="the ball")
    rig.robot.publish = publish
    vision.VISION.update(time=0.0, detections=[])
    check("a ball out of view: the sweep finds it after two 60-degree steps, without roaming",
          any("found the ball after turning 120 degrees" in o for o in rig.outcomes(start)), str(rig.outcomes(start)))
    check("... Gemini hears where", any(e["kind"] == "found" and "found the ball" in e["for_gemini"] for e in events_since(before)))
    blind.stop_beacon()

    reset()
    depth()
    asked = []

    def exists_at_third_look(url, path, payload, timeout=0):
        asked.append(payload["name"])
        return {"found": len(asked) >= 3, "source": "gemma"}

    vision.ask_eyes = exists_at_third_look
    start = len(rig.records)
    res = await call(rig, "find", what="my keys")
    vision.ask_eyes = no_eyes
    check("keys (no detector class): the reply says Gemma looks for them", res.ok and "doesn't know 'keys'" in res.message
          and "Gemma" in res.message, res.message)
    check("... found at the third look (after 120 degrees), by Gemma", any("found the keys after turning 120 degrees" in o
          and "gemma" in o for o in rig.outcomes(start)) and asked[:3] == ["keys"] * 3, f"{rig.outcomes(start)} asked {asked}")
    blind.stop_beacon()

    reset()
    depth()
    room = Room(rig, 6.0, 5.0, 4.5, 2.5, 0.0, {"chair": (1.0, 2.5)})  # the chair is 3.5 m behind the robot
    before, start, t0 = deaf.EVENTS.last_id, len(rig.records), rig.clock.now
    res = await call(rig, "find", what="a chair", max_seconds=120)
    room.close()
    outcomes = rig.outcomes(start)
    check("a chair behind the robot, beyond the sweep's sight: the roam finds it", any("found the chair after" in o and "legs" in o
          for o in outcomes), str(outcomes))
    check("... in under 120 s, never nearer a wall than 0.2 m", rig.clock.now - t0 < 120 and room.min_wall >= 0.2,
          f"{rig.clock.now - t0:.0f} s, nearest wall {room.min_wall:.2f} m")
    check("... never faster than walk and turn speeds", all(m.linear.x <= 0.4 + 1e-9 and abs(math.degrees(m.angular.z)) <= 60 + 1e-6
                                                           for m in tt.walk_twists(rig)))
    evs = events_since(before)
    check("... Gemini hears it was found, and that Pupper is walking up to it", any(e["kind"] == "found" and "found the chair" in e["for_gemini"]
          and "walking up to it" in e["for_gemini"] for e in evs), str([e["for_gemini"][:80] for e in evs]))
    check("... then walks up to it and beeps there, and says so", any("reached the chair" in o and "beeping" in o for o in outcomes), str(outcomes))
    check("... Gemini is told to send the person toward the beeping, not to stop it now, and to stop it on 'got it'",
          any(e["kind"] == "goto" and "walk toward the beeping" in e["for_gemini"] and "don't call beacon now" in e["for_gemini"]
              and "enabled=false" in e["for_gemini"] for e in evs), str([e["for_gemini"][:80] for e in evs]))
    res = await call(rig, "beacon", enabled=False)
    check("... and if Gemini stops it right away anyway, the robot keeps beeping and says why", not res.ok
          and blind.beaconing() and "only just started" in res.message and "got it" in res.message, res.message)
    for _ in range(int(blind.AUTO_GRACE_S) + 2):  # the person walks over to it
        await tt._real_sleep(0)
    res = await call(rig, "beacon", enabled=False)
    check("... once the person has had time, 'got it' stops it", res.ok and "stopped" in res.message and not blind.beaconing(), res.message)
    res = await call(rig, "stop")
    check("... and stop always ends it at once", res.ok and not blind.beaconing())
    check("... ending in locomotion with nothing wanted", rig.robot.controllers[LOCOMOTION_CONTROLLER] == "active"
          and blind.EXPLORING["name"] == "" and blind.WANTED["name"] == "")
    blind.stop_beacon()

    reset()
    depth()
    room = Room(rig, 6.0, 5.0, 1.0, 1.0, 90.0, {"white container": (5.0, 4.0)}, coco=False)
    asked = []

    def fake_exists(url, path, payload, timeout=0):
        asked.append(path)
        return {"found": room.visible(payload["name"]), "source": "gemma"}

    vision.ask_eyes = fake_exists
    start = len(rig.records)
    await call(rig, "find", what="the white container", max_seconds=200)
    vision.ask_eyes = no_eyes
    room.close()
    outcomes = rig.outcomes(start)
    check("a thing only Gemma can recognise, across the room: found by asking the eyes at each stop",
          any("found the white container" in o and "gemma" in o for o in outcomes) and asked and all(p == "/exists" for p in asked),
          f"{outcomes} asked {len(asked)}")
    check("... never nearer a wall than 0.2 m", room.min_wall >= 0.2, f"nearest {room.min_wall:.2f} m")
    blind.stop_beacon()

    reset()
    depth()
    room = Room(rig, 6.0, 5.0, 3.0, 2.5, 0.0, {})
    before, start = deaf.EVENTS.last_id, len(rig.records)
    await call(rig, "find", what="a unicorn", max_seconds=60)
    room.close()
    outcomes = rig.outcomes(start)
    check("nothing to find: gives up when the time is up, saying how much it covered", any("no unicorn found in 60 s" in o
          and "square metres" in o for o in outcomes), str(outcomes))
    check("... having moved through several cells, never nearer a wall than 0.2 m", len(room.cells) >= 4 and room.min_wall >= 0.2,
          f"{len(room.cells)} cells, nearest wall {room.min_wall:.2f} m")
    check("... no beeping, and Gemini hears that it ended", not blind.beaconing()
          and any(e["kind"] == "search" and "search ended" in e["for_gemini"] for e in events_since(before)))

    reset()
    room = Room(rig, 0.7, 0.7, 0.35, 0.35, 0.0, {})
    start = len(rig.records)
    await call(rig, "find", what="a ball", max_seconds=60)
    room.close()
    outcomes = rig.outcomes(start)
    check("boxed in: gives up after a few legs that could not start, without walking", any("boxed in" in o for o in outcomes)
          and room.walk_ticks == 0, f"walk ticks {room.walk_ticks}; {outcomes}")

    reset()
    vision.POSTERS.clear()
    res = await call(rig, "find", what="a chair", max_seconds=5)
    check("with no eyes service at all the reply warns it will only turn", res.ok and "eyes service is not running" in res.message, res.message)
    vision.update_freespace([{"name": "center", "closest_m": 2.0}], "metric", source="pi")
    vision.FREESPACE["time"] = 0.0  # idle: the last post is older than the trust window, but the eyes are alive
    res = await call(rig, "find", what="a chair", max_seconds=5)
    check("... but an idle eyes service (stale data, recent post) is not a fault", res.ok and "not running" not in res.message, res.message)

    # Seen on the robot: Gemini called find three times while one was running, and the queue grew to 413 s of
    # searches and walk-ups. A find while one is running or queued is turned away; after a stop it is fine again.
    reset()
    depth()
    first = await tools.call(rig.ts, "find", {"what": "the blue wagon", "max_seconds": 10})
    again = await tools.call(rig.ts, "find", {"what": "the blue wagon"})
    check("a second find while one is queued is turned away, saying it is already looking", first.ok and again.ok
          and "already" in again.message and rig.ts.commands.qsize() <= 1, again.message)
    await call(rig, "stop")
    res = await call(rig, "find", what="the blue wagon", max_seconds=5)
    check("... and after a stop a new find is accepted (no leftover 'already looking')", res.ok and "already" not in res.message,
          res.message)
    await tools.call(rig.ts, "find", {"what": "the blue wagon", "max_seconds": 10})
    await call(rig, "stop")
    await tools.call(rig.ts, "stand_up", {})  # something else queued after the stop
    res = await tools.call(rig.ts, "find", {"what": "the blue wagon", "max_seconds": 5})
    await rig.settle()
    check("... even with another move queued after the stop (the stopped search's flag is stale)",
          res.ok and "already" not in res.message, res.message)

    depth()
    await call(rig, "follow", enabled=True)
    room = Room(rig, 6.0, 5.0, 3.0, 2.5, 0.0, {})
    res = await call(rig, "find", what="a chair", max_seconds=5)
    room.close()
    check("find ends follow mode and says so", res.ok and "follow" in res.message.lower() and not rig.ts.following, res.message)
    rig.imu(45.0)
    rig.robot.send("/rosout", tt._Msg(name="gemini_walk", msg="Emergency stop triggered"))
    await rig.settle()
    res = await call(rig, "find", what="a chair")
    check("fallen: no search", not res.ok and "fell" in res.message, res.message)
    rig.close()


# ----------------------------------------------------------------------------- the beacon
async def beacon_tests() -> None:
    print("Blind mode D: the beacon (beep where the thing is)")
    reset()
    rig = make_rig()

    async def run_for(ticks: int) -> None:  # let the beacon's loop run on the fake clock
        for _ in range(ticks):
            await tt._real_sleep(0)

    with wave.open(str(blind.beep_file())) as w:
        check("the chirp is a short mono WAV", w.getnchannels() == 1 and 0.2 < w.getnframes() / w.getframerate() < 0.5,
              f"{w.getnchannels()} channels, {w.getnframes() / w.getframerate():.2f} s")
    res = await call(rig, "beacon", enabled=False)
    check("beacon off when it isn't beeping says so", res.ok and "wasn't" in res.message, res.message)
    before = deaf.EVENTS.last_id
    res = await call(rig, "beacon", enabled=True, seconds=10)
    check("beacon on: accepted, says how often, how long and what stops it", res.ok and "once a second" in res.message
          and "10 s" in res.message and "enabled=false" in res.message, res.message)
    check("... and the screen says BEEPING", any(e["display"] and e["display"]["text"] == "BEEPING" for e in events_since(before)))
    await run_for(3)
    check("... it is beeping", blind.beaconing() and blind.BEACON["beeps"] >= 1, str(blind.BEACON))
    deaf.DEAF_MODE = True
    res = deaf.hearing_detected(rig.ts, {"cls": "alarm", "score": 40.0})
    deaf.DEAF_MODE = False
    check("... and deaf mode doesn't alert on its own beep", "own beacon" in res.message, res.message)
    await run_for(40)
    check("... it stops by itself after the time asked (about one beep a second)", not blind.beaconing()
          and 9 <= blind.BEACON["beeps"] <= 11, str(blind.BEACON))

    await call(rig, "beacon", enabled=True)
    await run_for(2)
    res = await call(rig, "beacon", enabled=False)
    check("'got it' (beacon enabled=false) stops it", res.ok and "stopped" in res.message and not blind.beaconing(), res.message)

    await call(rig, "beacon", enabled=True)
    await run_for(2)
    res = await call(rig, "stand_up")
    check("any motion stops it first, and says so", res.ok and "beeping" in res.message and not blind.beaconing(), res.message)

    await call(rig, "beacon", enabled=True)
    await run_for(2)
    await call(rig, "stop")
    await run_for(3)
    check("the stop tool ends it", not blind.beaconing(), str(blind.BEACON))

    res = await call(rig, "beacon", enabled=True, seconds=1000)
    check("a long beacon is capped, with the cap in the reply", res.ok and f"{blind.BEACON_MAX_S:.0f} s" in res.message, res.message)
    blind.stop_beacon()
    blind.CAN_PLAY = lambda: False
    res = await call(rig, "beacon", enabled=True)
    check("no sound player on the robot: refused, naming the players it looks for", not res.ok and "pw-play" in res.message, res.message)
    reset()
    rig.close()


# ----------------------------------------------------------------------------- the debug view
async def debug_tests() -> None:
    print("Blind mode E: the debug view (what the rules see) and the stop row")
    reset()
    rig = make_rig()
    worst = max(abs(a - b) for lat, lon in ((20, 0), (40, -30), (10, 60), (60, 45))
                for p in [debug.project(lat, lon)] for a, b in zip(fisheye_geom.latlon_deg(p[0] * 1400, p[1] * 1050), (lat, lon)))
    check("project is the lens model's inverse (to within half a degree)", worst < 0.5, f"{worst:.2f} degrees")
    check("the stop row is Lab 5's 454 of 572", fisheye_geom.STOP_BOTTOM == 454 and debug.STOP_BOTTOM_DEFAULT == 454)
    depth()
    see(("chair", box(0.5, 0.4, 0.6)))
    st = debug.debug_state(rig.ts)
    chair = st["detections"][0]
    check("a close chair ahead: the state says it blocks, and why", chair["blocks_forward"] and chair["close"] and chair["ahead"]
          and st["verdict"]["by_detector"] and "chair" in st["verdict"]["forward"], str(chair))
    check("... with the depth strips, the rule lines and the edges of 'ahead' to draw",
          len(st["strips"]) == 5 and all(len(st["lines"][k]) > 5 for k in ("stop", "block", "slow"))
          and all(len(edge) > 5 for edge in st["lines"]["ahead"]))
    vision.FREESPACE["circle"] = [0.496, 0.497, 0.283, 0.38]
    see(("person", [0.163, 0.046, 0.814, 0.943]))
    ghost = debug.debug_state(rig.ts)["detections"][0]
    check("a box mostly outside the lens is listed as ignored, with the reason", ghost["counted"] is False
          and "lens" in ghost["ignored_because"], str(ghost))
    vision.FREESPACE["circle"] = list(vision.WHOLE_FRAME)
    vision.VISION.update(time=0.0, detections=[])

    res = debug.set_stop_bottom(430)
    check("stop_bottom 430 moves find's arrival row and the slowdown row with it", res.ok and "430" in res.message
          and abs(blind.ARRIVE_NEARNESS - 430 / 572) < 1e-9 and abs(blind.SLOW_NEARNESS - (430 - 120) / 572) < 1e-9, res.message)
    res = debug.set_stop_bottom(600)
    check("an out-of-range stop_bottom is refused with the range and Lab 5's value", not res.ok and "380-520" in res.message
          and "454" in res.message and blind.ARRIVE_NEARNESS == 430 / 572, res.message)
    debug.set_stop_bottom(454)
    check("back to 454", fisheye_geom.STOP_NEARNESS == 454 / 572 and blind.ARRIVE_NEARNESS == 454 / 572)
    rig.close()


async def declaration_tests() -> None:
    print("Blind mode: what Gemini reads")
    rig = make_rig()
    tt.described(rig, ["find", "beacon"], "blind")
    text = tt.prompt_sections("Blind mode")
    check("prompt.md has a 'Blind mode' section with 30+ words that names find and beacon",
          len(text.split()) >= 30 and "find" in text and "beacon" in text, f"{len(text.split())} words")
    rig.close()


GROUPS = {"vision": vision_tests, "walk": walk_up_tests, "find": find_tests, "beacon": beacon_tests, "debug": debug_tests,
          "declarations": declaration_tests}


def main(groups: list[str]) -> None:
    for name in groups or GROUPS:
        if name not in GROUPS:
            sys.exit(f"unknown blind mode test group {name!r}; choose from {list(GROUPS)}")
        asyncio.run(GROUPS[name]())
        print()
    if tt.FAIL:
        print(f"{tt.FAIL} check(s) failed, {tt.PASS} passed.")
        raise SystemExit(1)
    print(f"All {tt.PASS} blind mode checks passed.")


if __name__ == "__main__":
    main(sys.argv[1:])
