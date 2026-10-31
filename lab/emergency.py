"""Emergency contact: Pupper phones one person you chose, never 911.

When someone asks Pupper to get help or to call their contact, Gemini calls call_contact; collapse
watch (collapse_watch.py) also calls the contact itself when nobody answers. The robot server places a real phone call through Twilio: the contact's phone rings, and when they
pick up a voice reads Pupper's message twice ("Pupper here. <reason>").

Who to call lives on the robot, outside the repo (it holds a Twilio token):
~/.config/gemini-pupper/emergency_contact.json

    {"name": "Sam", "to": "+15551234567", "from": "+15557654321",
     "account_sid": "AC...", "auth_token": "..."}

"from" is your Twilio number. There is no number parameter: Gemini can only ever call this one
person, so it can't dial 911 or anyone else. Without the file the tool refuses and says why.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

from robot_api import Result, Robot
from tool_registry import Param, tool

log = logging.getLogger("emergency")

CONTACT_FILE = Path.home() / ".config" / "gemini-pupper" / "emergency_contact.json"
FIELDS = ("name", "to", "from", "account_sid", "auth_token")
MIN_CALL_GAP_S = 60.0  # one call a minute: Gemini may ask twice in one breath
_last_call = 0.0


# ============================================================ what the robot does
def load_contact() -> tuple[dict | None, str]:
    """The contact file's fields, or None and what is wrong with it."""
    try:
        contact = json.loads(CONTACT_FILE.read_text())
    except FileNotFoundError:
        return None, f"no emergency contact is set up (the robot needs {CONTACT_FILE})"
    except (OSError, ValueError) as e:
        return None, f"the emergency contact file can't be read ({type(e).__name__})"
    missing = [f for f in FIELDS if not str(contact.get(f, "")).strip()]
    if missing:
        return None, f"the emergency contact file is missing {', '.join(missing)}"
    return contact, ""


def masked(number: str) -> str:
    return f"***{number[-4:]}"


def _place_call(contact: dict, message: str) -> tuple[bool, str]:
    """Ask Twilio to ring the contact and read ``message``. Blocking: run it in a thread."""
    twiml = f'<Response><Say loop="2">{escape(message)}</Say></Response>'
    body = urllib.parse.urlencode({"To": contact["to"], "From": contact["from"], "Twiml": twiml}).encode()
    auth = base64.b64encode(f"{contact['account_sid']}:{contact['auth_token']}".encode()).decode()
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{contact['account_sid']}/Calls.json",
        data=body, headers={"Authorization": f"Basic {auth}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return True, json.loads(r.read()).get("status", "queued")
    except urllib.error.HTTPError as e:
        try:
            why = json.loads(e.read()).get("message", "")
        except ValueError:
            why = ""
        return False, f"Twilio said {e.code} {why}".strip()
    except (urllib.error.URLError, TimeoutError) as e:
        return False, f"no internet to reach Twilio ({e})"


async def call_contact(robot: Robot, reason: str) -> Result:
    """Phone the emergency contact and read them ``reason``."""
    global _last_call
    contact, problem = load_contact()
    if contact is None:
        return Result(False, f"can't call: {problem}. I can't call anyone else, and never 911")
    who = f"{contact['name']} at {masked(contact['to'])}"
    since = time.time() - _last_call
    if since < MIN_CALL_GAP_S:
        return Result(False, f"already called {contact['name']} {since:.0f} s ago; I wait"
                             f" {MIN_CALL_GAP_S:.0f} s between calls")
    message = f"Pupper here. {reason.strip() or 'Someone asked me to call you for help.'}"
    ok, how = await asyncio.to_thread(_place_call, contact, message)
    if not ok:
        log.warning("call to %s failed: %s", who, how)
        return Result(False, f"couldn't call {who}: {how}")
    _last_call = time.time()
    log.info("calling %s (%s)", who, how)
    return Result(True, f"calling {who} now: their phone is ringing, and they will hear: \"{message}\"")


# ============================================================ what Gemini reads
@tool(
    "call_contact",
    "Phone the person's emergency contact (one person set up on the robot, e.g. a friend or"
    " family member) and read them a short message. Use it when someone asks you to call for help,"
    " call their contact, or get someone. It can only call that one contact: never 911 or any"
    " emergency service. If someone needs 911, tell them to call it themselves.",
    reason=Param("string", "What the contact should hear, in one or two short sentences, e.g."
                 " 'The fire alarm is going off and Alex asked for help.'"),
)
async def call_contact_tool(robot: Robot, reason: str) -> Result:
    return await call_contact(robot, reason)
