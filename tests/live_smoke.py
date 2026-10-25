"""Essai sur le vrai téléphone, sans afficher le contenu des messages : uv run python tests/live_smoke.py"""

import re
import time

from nexus_android import server as s
from nexus_android.server import Step


def text_of(result):
    return result if isinstance(result, str) else result[0]


print("phone:", s._status().text)
view = s._view("elements")
print("look:", text_of(view).splitlines()[0], "·", len(text_of(view).splitlines()) - 1, "elements")
image = s._view("image")
print("image:", type(image[1]).__name__, len(image[1].data), "bytes")

listing = text_of(s._messages(None, None, 10, 0))
ids = re.findall(r"^\[(\d+)\]", listing, re.M)
assert ids, "no conversation read"
print("conversations:", len(ids), "· unread:", listing.count("UNREAD"))
thread = text_of(s._messages(int(ids[0]), None, 10, 0))
print("conversation read:", len(thread.splitlines()) - 1, "messages ·", thread.count("[masked code]"), "masked codes")

out = s._draft("0600000000", "NexusAndroid test: prepared reply, accents éàç.", "sms")
assert "éàç" in text_of(out), "draft not on screen"
print("draft visible: yes")
typed = s._act([Step(do="clear", target="NexusAndroid test"), Step(do="type", text="Été à ç ô 🧪")], "none")
assert "✓ 2." in text_of(typed), "accented typing not read back: " + text_of(typed)
print("accents and emoji typed and read back: yes")
cleanup = s._act([Step(do="clear", target="Été à ç"), Step(do="key", keys="back"), Step(do="key", keys="back"),
                  Step(do="key", keys="home")], "none")
print("cleanup:", text_of(cleanup).replace("\n", " | "))
assert "✗" not in text_of(cleanup), "draft cleanup incomplete"
print("notifications:", text_of(s.extras.notifications(s._init()[0])).splitlines()[0][:80])
print("fold posture:", s._init()[1].current_posture() or "?")
time.sleep(0.5)
print("checkup:\n" + s._checkup(False))
