#!/usr/bin/env python3
"""Desk tests for the emergency contact call (lab/emergency.py). No robot, no network, no Twilio.

    python3 lab/test.py emergency   # just these

A temporary contact file stands in for ~/.config/gemini-pupper/emergency_contact.json, and a
fake _place_call records the call instead of phoning anyone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_tools as tt  # noqa: E402  a fake ROS, and lab/ loaded
from test_tools import Rig, check, tools  # noqa: E402

import emergency  # noqa: E402

CONTACT = {"name": "Sam", "to": "+15551234567", "from": "+15557654321",
           "account_sid": "ACtest", "auth_token": "not-a-real-token"}


async def emergency_tests() -> None:
    print("Emergency contact: Pupper phones one person, never 911")
    rig = Rig()
    tmp = Path(tempfile.mkdtemp())
    emergency.CONTACT_FILE = tmp / "emergency_contact.json"
    placed: list[tuple[dict, str]] = []
    reply = [(True, "queued")]

    def fake_call(contact: dict, message: str) -> tuple[bool, str]:
        placed.append((contact, message))
        return reply[0]

    emergency._place_call = fake_call
    logging.getLogger("emergency").addHandler(logging.NullHandler())  # the failed-call warning is expected
    logging.getLogger("emergency").propagate = False

    res = await tools.call(rig.ts, "call_contact", {"reason": "The fire alarm is going off."})
    check("with no contact file it refuses, says what is missing, and calls no one",
          not res.ok and "emergency_contact.json" in res.message and not placed, res.message)
    emergency.CONTACT_FILE.write_text(json.dumps({**CONTACT, "to": ""}))
    res = await tools.call(rig.ts, "call_contact", {"reason": "help"})
    check("a contact file without a number is refused, naming the field", not res.ok and "to" in res.message
          and not placed, res.message)

    emergency.CONTACT_FILE.write_text(json.dumps(CONTACT))
    res = await tools.call(rig.ts, "call_contact", {"reason": "The fire alarm is going off."})
    check("it calls the contact and reads them the reason", res.ok and len(placed) == 1
          and placed[0][0]["to"] == CONTACT["to"] and "fire alarm" in placed[0][1], res.message)
    check("... Gemini hears who it is calling, with the number masked", "Sam" in res.message
          and "***4567" in res.message and CONTACT["to"] not in res.message, res.message)
    check("... and never the Twilio token", CONTACT["auth_token"] not in res.message)
    res = await tools.call(rig.ts, "call_contact", {"reason": "again"})
    check("a second call right after is refused, with the wait", not res.ok and "already called" in res.message
          and str(int(emergency.MIN_CALL_GAP_S)) in res.message and len(placed) == 1, res.message)

    emergency._last_call = 0.0
    reply[0] = (False, "Twilio said 400 The number is unverified.")
    res = await tools.call(rig.ts, "call_contact", {"reason": "help"})
    check("a failed call says so, with Twilio's reason", not res.ok and "unverified" in res.message, res.message)

    decl = next(d for d in tools.declarations(rig.ts) if d["name"] == "call_contact")
    check("the tool has no number to dial: only the reason", set(decl["parameters"]["properties"]) == {"reason"},
          str(decl["parameters"]))
    check("its description says it never calls 911", "never 911" in decl["description"], decl["description"])
    prompt = tt.system_prompt()
    check("the prompt tells Gemini to use call_contact, which can never call 911",
          "call_contact" in prompt and "never call 911" in prompt)
    rig.close()


if __name__ == "__main__":
    asyncio.run(emergency_tests())
    if tt.FAIL:
        print(f"{tt.FAIL} check(s) failed, {tt.PASS} passed.")
        sys.exit(1)
    print(f"All {tt.PASS} emergency contact checks passed.")
