"""Essai sur le vrai téléphone, sans afficher le contenu des messages : uv run python tests/live_smoke.py"""

import re
import time

from nexus_android import server as s
from nexus_android.server import Step


def text_of(result):
    return result if isinstance(result, str) else result[0]


print("phone :", s._status().text)
view = s._view("elements")
print("look :", text_of(view).splitlines()[0], "·", len(text_of(view).splitlines()) - 1, "éléments")
image = s._view("image")
print("image :", type(image[1]).__name__, len(image[1].data), "octets")

listing = text_of(s._messages(None, None, 10, 0))
ids = re.findall(r"^\[(\d+)\]", listing, re.M)
assert ids, "aucune conversation lue"
print("conversations :", len(ids), "· non lues :", listing.count("NON LU"))
thread = text_of(s._messages(int(ids[0]), None, 10, 0))
print("conversation lue :", len(thread.splitlines()) - 1, "messages ·", thread.count("[masked code]"), "masked codes")

out = s._draft("0600000000", "Essai NexusAndroid : réponse préparée, accents éàç.", "sms")
assert "éàç" in text_of(out), "brouillon absent de l'écran"
print("brouillon visible : oui")
typed = s._act([Step(do="clear", target="Essai NexusAndroid"), Step(do="type", text="Été à ç ô 🧪")], "none")
assert "✓ 2." in text_of(typed), "saisie avec accents non relue : " + text_of(typed)
print("accents et emoji saisis et relus : oui")
cleanup = s._act([Step(do="clear", target="Été à ç"), Step(do="key", keys="back"), Step(do="key", keys="back"),
                  Step(do="key", keys="home")], "none")
print("nettoyage :", text_of(cleanup).replace("\n", " | "))
assert "✗" not in text_of(cleanup), "nettoyage du brouillon incomplet"
print("notifications :", text_of(s.extras.notifications(s._init()[0])).splitlines()[0][:80])
print("pliage :", s._init()[1].current_posture() or "?")
time.sleep(0.5)
print("checkup :\n" + s._checkup(False))
