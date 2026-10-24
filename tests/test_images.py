"""Règle commune des images (NexusHarness/shared/images.md) : taille, WebP, plafonds, image inchangée non renvoyée."""

from __future__ import annotations

import io
import random

import pytest
from PIL import Image as PILImage
from test_screen_server import NODES, STATUS, fast, jpeg, payload  # noqa: F401  (fixture fast)

from nexus_android import screen, server


def test_size_rule_long_side_and_megapixels():
    for w, h in ((1248, 1972), (2448, 1848), (1080, 2400), (3000, 400)):
        f = screen.fit(w, h)
        assert max(w, h) * f <= screen.MAX_SIDE + 0.5 and w * h * f * f <= screen.MAX_PIXELS + 2
    assert screen.fit(800, 600) == 1.0  # assez petite : jamais agrandie


def _ui(size=(853, 1348)) -> PILImage.Image:
    """Écran d'application : aplats et quelques barres, comme une liste de Réglages."""
    image = PILImage.new("RGB", size, (16, 18, 20))
    for y in range(0, size[1], 140):
        image.paste((40, 44, 48), (20, y + 20, size[0] - 20, y + 120))
    return image


def _photo(size=(1234, 931), seed=1) -> PILImage.Image:
    rnd = random.Random(seed)
    return PILImage.frombytes("RGB", size, bytes(rnd.getrandbits(8) for _ in range(size[0] * size[1] * 3)))


def test_simple_screens_are_lossless_webp():
    data, size = screen.encode(_ui())
    back = PILImage.open(io.BytesIO(data))
    assert back.format == "WEBP" and len(data) <= screen.LOSSLESS_MAX
    assert back.convert("RGB").tobytes() == _ui().tobytes()  # sans perte : pixels identiques


def test_heavy_images_fall_back_and_never_exceed_the_cap():
    data, _ = screen.encode(_photo())
    assert PILImage.open(io.BytesIO(data)).format == "WEBP" and len(data) <= screen.IMAGE_MAX
    assert len(screen.encode(_photo(), photo=True)[0]) <= screen.IMAGE_MAX


def test_the_same_image_is_not_sent_twice(fast, monkeypatch):  # noqa: F811
    monkeypatch.setattr(screen, "UNCHANGED", True)
    fake, adb, scr = fast
    fake.raw = payload(STATUS, (1248, 1972), NODES, jpeg((810, 1280)), (1248, 1972))
    first = server._view("both")
    assert len(first) == 2 and PILImage.open(io.BytesIO(first[1].data)).format == "WEBP"
    again = server._view("both")
    assert len(again) == 1 and "image unchanged since the last one sent" in again[0].splitlines()[0]
    assert "[N] marks drawn" not in again[0].splitlines()[0]  # rien de dessiné n'est annoncé sans image
    assert len(server._view("both", marks="none")) == 2  # autre rendu (sans numéros) : envoyé
    fake.raw = payload(STATUS, (1248, 1972), NODES, jpeg((810, 1280), color="gray"), (1248, 1972))
    assert len(server._view("both", marks="none")) == 2  # l'écran a changé : envoyé


def test_a_tiny_change_seen_by_the_tree_resends_the_image(fast, monkeypatch):  # noqa: F811
    """Un interrupteur basculé change à peine l'empreinte 64×64 mais change l'arbre : l'image repart."""
    monkeypatch.setattr(screen, "UNCHANGED", True)
    fake, adb, scr = fast
    fake.raw = payload(STATUS, (1248, 1972), NODES, jpeg((810, 1280)), (1248, 1972))
    assert len(server._view("image")) == 2
    changed = [dict(n) for n in NODES]
    target = next(n for n in changed if n.get("text") and n.get("package") == "com.app")
    target["text"] += " (activé)"
    fake.raw = payload(STATUS, (1248, 1972), changed, jpeg((810, 1280)), (1248, 1972))
    assert len(server._view("image")) == 2  # même image, arbre différent : envoyée


def test_a_shared_daemon_client_without_session_always_gets_the_image(fast, monkeypatch):  # noqa: F811
    """Relecture Antigravity : sans en-tête de session, les agents partagent la vue commune du démon ; l'image qu'un
    autre a reçue ne doit jamais être dite « inchangée » à celui qui ne l'a pas vue."""
    monkeypatch.setattr(screen, "UNCHANGED", True)
    monkeypatch.setattr(server, "_SHARED", True)
    fake, adb, scr = fast
    fake.raw = payload(STATUS, (1248, 1972), NODES, jpeg((810, 1280)), (1248, 1972))
    assert len(server._view("image")) == 2
    assert len(server._view("image")) == 2  # vue commune : renvoyée
    token = server._SESSION.set("session-codex")
    try:
        assert len(server._view("image")) == 2
        assert len(server._view("image")) == 1  # session propre : la même image n'est pas renvoyée
    finally:
        server._SESSION.reset(token)


def test_mms_photo_is_turned_upright():
    picture = PILImage.new("RGB", (400, 200), "white")
    exif = picture.getexif()
    exif[0x0112] = 6  # Orientation : pivotée de 90° (photo portrait de l'appareil)
    raw = io.BytesIO()
    picture.save(raw, "JPEG", exif=exif.tobytes())
    upright = server.ImageOps.exif_transpose(PILImage.open(io.BytesIO(raw.getvalue())))
    assert upright.size == (200, 400)


def test_mms_photos_follow_the_rule():
    data, _ = screen.encode(_photo((1200, 900)), photo=True)
    assert PILImage.open(io.BytesIO(data)).format == "WEBP" and len(data) <= screen.IMAGE_MAX


@pytest.mark.parametrize("size", [(1248, 1972), (2448, 1848)])
def test_rendered_look_respects_the_limits(fast, size):  # noqa: F811
    fake, adb, scr = fast
    fake.raw = payload(STATUS, size, NODES, jpeg(size), size)
    out = server._view("image")
    image = PILImage.open(io.BytesIO(out[1].data))
    assert max(image.size) <= screen.MAX_SIDE and image.width * image.height <= screen.MAX_PIXELS + 2
    assert len(out[1].data) <= screen.IMAGE_MAX


# ---------------------------------------------------------------- relecture Codex (règle des images)


def test_scale_follows_an_image_shrunk_to_fit_500_kb(monkeypatch):
    """Image réduite encore par encode (plus de 500 Ko) : l'échelle des clics suit la taille vraiment envoyée."""
    scr = screen.Screen(object())  # type: ignore[arg-type]
    scr.size = (853, 1348)
    prepared = scr.prepare(_photo((853, 1348)))
    assert scr.scale == 1.0
    monkeypatch.setattr(screen, "encode", lambda image, photo=False: (b"x", (639, 1010)))
    scr.send(prepared)
    assert scr.scale == pytest.approx(639 / 853)


def test_an_unchanged_look_keeps_the_scale_of_the_image_actually_sent(fast, monkeypatch):  # noqa: F811
    """Relecture Codex : image réduite sous 500 Ko au premier regard ; au second, « inchangée », les coordonnées
    gardent l'échelle de l'image que l'agent a reçue."""
    monkeypatch.setattr(screen, "UNCHANGED", True)
    monkeypatch.setattr(screen, "encode", lambda image, photo=False: (b"RIFF", (639, 1010)))
    fake, adb, scr = fast
    fake.raw = payload(STATUS, (1248, 1972), NODES, jpeg((853, 1348)), (1248, 1972))
    first = server._view("image")
    sent_scale = scr.scale
    assert "image 639×1010" in first[0].splitlines()[0] and sent_scale == pytest.approx(639 / 1248)
    again = server._view("image")
    assert len(again) == 1 and "image 639×1010" in again[0].splitlines()[0]
    assert scr.scale == pytest.approx(sent_scale)


def test_a_text_only_look_keeps_the_scale_of_the_last_image(fast, monkeypatch):  # noqa: F811
    """Relecture Codex : image réduite sous 500 Ko, puis look(elements) ou then=changes sans image : les coordonnées
    restent celles de l'image que l'agent a ; un pliage (autre taille) les recale."""
    monkeypatch.setattr(screen, "encode", lambda image, photo=False: (b"RIFF", (639, 1010)))
    fake, adb, scr = fast
    fake.raw = payload(STATUS, (1248, 1972), NODES, jpeg((853, 1348)), (1248, 1972))
    server._view("image")
    sent_scale = scr.scale
    fake.raw = payload(STATUS, (1248, 1972), NODES)
    server._view("elements")
    server._view("changes")
    assert scr.scale == pytest.approx(sent_scale)
    fake.raw = payload(STATUS, (2448, 1848), NODES)
    server._view("elements")
    assert scr.scale == pytest.approx(screen.fit(2448, 1848))


def test_text_read_by_ocr_counts_as_content(fast, monkeypatch):  # noqa: F811
    """Relecture Codex : un chiffre lu par OCR qui change (1 → 7) sur une image presque identique : renvoyée."""
    monkeypatch.setattr(screen, "UNCHANGED", True)
    fake, adb, scr = fast
    digit = {"text": "1"}
    monkeypatch.setattr(screen, "needs_ocr", lambda *a, **k: True)
    monkeypatch.setattr(server, "_ocr_elements", lambda s, image=None: [
        screen.Element(0, "OCR", digit["text"], "", "", "com.app", (400, 600, 420, 620), True)])
    fake.raw = payload(STATUS, (1248, 1972), NODES, jpeg((810, 1280)), (1248, 1972))
    assert len(server._view("image")) == 2
    digit["text"] = "7"
    assert len(server._view("image")) == 2


def test_a_fold_or_rotation_resends_the_image(fast, monkeypatch):  # noqa: F811
    monkeypatch.setattr(screen, "UNCHANGED", True)
    fake, adb, scr = fast
    fake.raw = payload(STATUS, (1248, 1972), NODES, jpeg((1248, 1972)), (1248, 1972))
    assert len(server._view("image")) == 2
    fake.raw = payload(STATUS, (2448, 1848), NODES, jpeg((2448, 1848)), (2448, 1848))
    assert len(server._view("image")) == 2  # autre écran du Fold, même contenu : autre géométrie, envoyée


def test_red_and_green_are_not_the_same_image():
    red, green = PILImage.new("RGB", (100, 100), (255, 0, 0)), PILImage.new("RGB", (100, 100), (0, 130, 0))
    assert red.convert("L").getpixel((0, 0)) == green.convert("L").getpixel((0, 0))  # même gris
    assert not screen.same_thumb(screen.thumb(red), screen.thumb(green))


def test_the_content_print_sees_the_system_bar():
    tile = screen.Element(1, "Switch", "Wi-Fi", "", "", "com.android.systemui", (0, 0, 100, 50), True, states=["checked"])
    off = screen.Element(1, "Switch", "Wi-Fi", "", "", "com.android.systemui", (0, 0, 100, 50), True)
    assert screen.fingerprint([tile], "x") == screen.fingerprint([off], "x")  # boucles : barre système ignorée
    assert screen.content_print([tile], "x") != screen.content_print([off], "x")  # « inchangée » : vue


@pytest.mark.parametrize("size", [(2448, 1848), (1569, 500), (4000, 3000)])
def test_sizes_never_exceed_the_caps(size):
    assert screen.within(screen.sized(*size))
