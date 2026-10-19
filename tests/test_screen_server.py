"""Lecteur d'écran du serveur gardé en vie (requête `screen` de phone/NexusQuery.java), sans téléphone : même liste
d'éléments que uiautomator dump, un seul échange pour look, repli par uiautomator après avoir rendu la connexion."""

from __future__ import annotations

import io
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image as PILImage

from nexus_android import query, screen, server
from nexus_android.query import Table
from nexus_android.screen import NODE_FLAGS, POSTURE_MARK, STATE_SH, Screen, elements_of, parse_snapshot, parse_ui

JAVA = Path(__file__).resolve().parents[1] / "phone" / "NexusQuery.java"
TOKEN = "0123456789abcdef" * 2  # gitleaks:allow (valeur factice de test)


def _int(value: int) -> bytes:
    return struct.pack(">i", value)


def _text(value: str | None) -> bytes:
    if value is None:
        return _int(-1)
    data = value.encode()
    return _int(len(data)) + data


def payload(status: str | None, size: tuple[int, int], nodes: list[dict[str, str]] | str,
            image: bytes | str | None = None, shot: tuple[int, int] = (0, 0)) -> bytes:
    """Réponse `screen` telle que l'écrit NexusQuery.snapshot (nodes : attributs ; texte : erreur de l'arbre ; image :
    JPEG, texte d'erreur, ou None si non demandée)."""
    out = [_text(status), _int(0), _int(size[0]), _int(size[1])]
    if isinstance(nodes, str):
        out += [_int(1), _text(nodes)]
    else:
        out += [_int(0), _int(len(nodes))]
        for a in nodes:
            out += [_text(a.get(k, "")) for k in ("text", "resource-id", "class", "package", "content-desc")]
            out.append(_int(sum(1 << bit for bit, name in enumerate(NODE_FLAGS) if a.get(name) == "true")))
            x1, y1, x2, y2 = map(int, re.findall(r"-?\d+", a["bounds"]))
            out += [_int(x1), _int(y1), _int(x2), _int(y2)]
    if image is None:
        out.append(_int(2))
    elif isinstance(image, str):
        out += [_int(1), _text(image)]
    else:
        out += [_int(0), _int(shot[0]), _int(shot[1]), _int(len(image)), image]
    return b"".join(out)


def jpeg(size: tuple[int, int], color: str = "white") -> bytes:
    out = io.BytesIO()
    PILImage.new("RGB", size, color).save(out, "JPEG", quality=90)
    return out.getvalue()


UI = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation="0">
<node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.app" content-desc=""
 checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="true"
 long-clickable="false" password="false" selected="false" bounds="[0,0][1248,1972]">
<node index="0" text="Wi-Fi" resource-id="com.app:id/switch" class="android.widget.Switch" package="com.app"
 content-desc="" checkable="true" checked="true" clickable="true" enabled="false" focusable="true" focused="false"
 scrollable="false" long-clickable="false" password="false" selected="true" bounds="[0,100][1248,200]" />
<node index="1" text="Ligne 1&#10;Ligne 2" resource-id="" class="android.widget.TextView" package="com.app"
 content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false"
 scrollable="false" long-clickable="true" password="false" selected="false" bounds="[0,300][600,400]" />
<node index="2" text="Votre code de vérification est 482913" resource-id="" class="android.widget.TextView"
 package="com.google.android.apps.messaging" content-desc="" checkable="false" checked="false" clickable="false"
 enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false"
 selected="false" bounds="[100,600][1100,700]" />
<node index="3" text="" resource-id="com.app:id/pin" class="android.widget.EditText" package="com.app"
 content-desc="PIN" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="true"
 scrollable="false" long-clickable="false" password="true" selected="false" bounds="[0,1800][880,1900]" />
<node index="4" text="" resource-id="" class="android.view.View" package="com.app" content-desc="" checkable="false"
 checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false"
 long-clickable="false" password="false" selected="false" bounds="[0,0][0,0]" />
</node></hierarchy>"""
NODES = [dict(n.attrib) for n in ET.fromstring(UI[UI.find("<hierarchy"):]).iter("node")]
STATUS = ("  mWakefulness=Awake\n  mCurrentFocus=Window{abc u0 com.app/com.app.Main}\n    isKeyguardShowing=false\n"
          f"{POSTURE_MARK}\n  mCommittedState=Optional[DeviceState{{identifier=3, name='OPENED', app_accessible=true}}]\n")


def _key(e: screen.Element) -> tuple:
    return (e.index, e.role, e.text, e.desc, e.rid, e.package, e.bounds, e.clickable, e.states, e.password, e.focused,
            e.editable, e.scrollable, e.masked)


class FakeServer:
    def __init__(self, raw: bytes | None) -> None:
        self.raw = raw
        self.queries: list[list[str]] = []
        self.released = 0

    def run(self, queries: list[list[str]]) -> list[Table] | None:
        self.queries += queries
        return None if self.raw is None else [Table(raw=self.raw)]

    def release(self) -> None:
        self.released += 1


class FakeAdb:
    """adb simulé : enregistre chaque commande ; répond à l'ancienne méthode (dumpsys, screencap, uiautomator)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        png = io.BytesIO()
        PILImage.new("RGB", (1248, 1972), "white").save(png, "PNG")
        self.png = png.getvalue()

    def shell(self, *argv: str, timeout: float = 30.0, check: bool = True) -> str:
        self.calls.append(argv)
        if argv[0] == "sh" and "uiautomator dump" in argv[2]:
            return "UI hierchary dumped to: x\n" + UI + f"\n{screen.STATE_MARK}\n" + STATUS.split(POSTURE_MARK)[0]
        if argv[0] == "sh" and argv[2] == STATE_SH:
            return STATUS.split(POSTURE_MARK)[0]
        if argv[:2] == ("dumpsys", "device_state"):
            return STATUS.split(POSTURE_MARK)[1]
        return ""

    def exec_out(self, *argv: str, timeout: float = 30.0) -> bytes:
        self.calls.append(argv)
        return self.png


@pytest.fixture
def fast(monkeypatch):
    """Lecteur d'écran simulé (réponse à fixer par le test) branché sur un adb simulé."""
    monkeypatch.setattr(screen, "FAST", True)
    fake = FakeServer(None)
    monkeypatch.setattr(query, "server_for", lambda adb: fake)
    adb = FakeAdb()
    scr = Screen(adb)  # type: ignore[arg-type]
    monkeypatch.setattr(server, "ADB", adb)
    monkeypatch.setattr(server, "SCREEN", scr)
    monkeypatch.setattr(server, "MESSAGES", object())
    return fake, adb, scr


# ---------------------------------------------------------------- même liste que uiautomator dump


def test_served_nodes_give_the_same_elements_as_the_xml():
    snap = parse_snapshot(payload(STATUS, (1248, 1972), NODES))
    assert snap.nodes is not None
    served, dumped = screen.mask_elements(elements_of(snap.nodes)), screen.mask_elements(parse_ui(UI))
    assert [_key(e) for e in served] == [_key(e) for e in dumped]
    assert [e.text for e in served][:3] == ["", "Wi-Fi", "Ligne 1\nLigne 2"]  # retour à la ligne gardé, comme le XML
    assert served[1].states == ["checked", "selected", "disabled"]
    assert served[3].masked and "482913" not in served[3].text
    assert snap.state and snap.state.awake and not snap.state.locked and snap.state.package == "com.app"
    assert snap.posture == "OPENED" and snap.size == (1248, 1972)


def test_snapshot_errors_and_truncation():
    snap = parse_snapshot(payload(None, (0, 0), "ERROR: null root node", "no screenshot"))
    assert snap.nodes is None and snap.tree_error.startswith("ERROR") and snap.image_error == "no screenshot"
    assert snap.state is None
    with pytest.raises(ValueError):
        parse_snapshot(payload(STATUS, (1248, 1972), NODES)[:-3])


def test_binary_answer_in_the_query_protocol():
    raw = payload(STATUS, (10, 10), [])
    data = query.MAGIC + _text(TOKEN) + _int(2) + _int(len(raw)) + raw
    (table,) = query.decode(data, 1, TOKEN)
    assert table.raw == raw and not table.rows and not table.error


# ---------------------------------------------------------------- look en un échange


def test_look_in_one_exchange(fast):
    fake, adb, scr = fast
    fake.raw = payload(STATUS, (1248, 1972), NODES, jpeg((810, 1280)), (1248, 1972))
    out = server._view("both")
    assert adb.calls == []  # ni dumpsys, ni screencap, ni uiautomator
    assert fake.queries == [["screen", "--image", "scaled", "--max", "1280", "--quality", str(screen.PHONE_QUALITY)]]
    header = out[0].splitlines()[0]
    assert header.startswith("Foreground: com.app") and "image 810×1280" in header
    assert scr.size == (1248, 1972) and scr.posture == "OPENED" and scr.scale == pytest.approx(1280 / 1972)
    image = PILImage.open(io.BytesIO(out[1].data))
    assert image.size == (810, 1280)
    # Code masqué : zone noircie sur l'image réduite (limites écran × réduction du téléphone).
    x, y = round(600 * 810 / 1248), round(650 * 1280 / 1972)
    assert max(image.convert("L").getpixel((x, y)), 0) < 40  # type: ignore[arg-type]
    assert "482913" not in out[0]
    assert [e.text or e.desc for e in scr.elements] == [e.text or e.desc for e in screen.mask_elements(parse_ui(UI))]


def test_protected_app_releases_the_connection_at_once(fast, monkeypatch):
    """Applis bancaires et d'identité : elles repèrent une accessibilité active, rendue sitôt la lecture faite."""
    fake, adb, scr = fast
    fake.raw = payload(STATUS, (1248, 1972), NODES, jpeg((810, 1280)), (1248, 1972))
    server._view("both")
    assert fake.released == 0
    monkeypatch.setattr(server.policy, "protected_app", lambda package: "Bank" if package == "com.app" else None)
    out = server._view("both")
    assert fake.released == 1 and "Bank: read-only" in out[0].splitlines()[0]


def test_region_asks_for_the_full_resolution(fast):
    fake, adb, scr = fast
    scr.scale = 0.5
    fake.raw = payload(STATUS, (1248, 1972), NODES, jpeg((1248, 1972)), (1248, 1972))
    out = server._view("image", region=[0, 0, 200, 100])
    assert fake.queries[-1][2] == "full" and adb.calls == []
    assert PILImage.open(io.BytesIO(out[1].data)).size == (400, 200)


def test_elements_only_sets_the_scale_without_an_image(fast):
    fake, adb, scr = fast
    scr.size = None
    fake.raw = payload(STATUS, (1248, 1972), NODES)
    server._view("elements")
    assert fake.queries[-1][2] == "none" and adb.calls == []
    assert scr.size == (1248, 1972) and scr.scale == pytest.approx(1280 / 1972)


def test_tree_error_releases_then_uiautomator(fast):
    fake, adb, scr = fast
    fake.raw = payload(STATUS, (1248, 1972), "UI tree unavailable (busy)", jpeg((810, 1280)), (1248, 1972))
    out = server._view("both")
    assert fake.released == 1
    assert any(c[0] == "sh" and "uiautomator dump" in c[2] for c in adb.calls)
    assert not any(c[0] == "screencap" for c in adb.calls)  # image du lecteur gardée
    assert len(out) == 2 and "482913" not in out[0]


def test_server_unavailable_falls_back_to_the_old_reads(fast):
    fake, adb, scr = fast
    fake.raw = None
    out = server._view("both")
    assert any(c[0] == "screencap" for c in adb.calls)
    assert any(c[0] == "sh" and "uiautomator dump" in c[2] for c in adb.calls)
    assert len(out) == 2 and scr.shrink == 1.0
    assert "image 810×1280" in out[0].splitlines()[0]


def test_dump_before_a_gesture_uses_the_reader(fast):
    fake, adb, scr = fast
    fake.raw = payload(STATUS, (1248, 1972), NODES)
    assert [e.text for e in scr.dump()] == [e.text for e in screen.mask_elements(parse_ui(UI))]
    assert adb.calls == [] and scr.last_state and scr.last_state.package == "com.app"


def test_status_missing_reads_the_state_by_shell(fast):
    fake, adb, scr = fast
    fake.raw = payload(None, (1248, 1972), NODES)
    reading = scr.read(image=False, tree=True)
    assert reading.state.package == "com.app" and adb.calls == [("sh", "-c", STATE_SH)]


# ---------------------------------------------------------------- le serveur Java


def _java_string(name: str) -> str:
    source = JAVA.read_text(encoding="utf-8")
    match = re.search(rf"static final String {name} = (.*?);\n", source, re.S)
    assert match, name
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1)))


def test_java_state_command_is_the_python_one():
    assert _java_string("STATE_SH") == STATE_SH
    assert _java_string("POSTURE_MARK") == POSTURE_MARK


def test_java_reader_is_read_only():
    source = JAVA.read_text(encoding="utf-8")
    for forbidden in ("injectInputEvent", "performGlobalAction", "performAction", "executeShellCommand",
                      "adoptShellPermissionIdentity", "setRotation", "grantRuntimePermission", "setClipboard"):
        assert forbidden not in source, forbidden
    assert "FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES" in source  # services de l'utilisateur jamais suspendus
    assert "uid != SHELL_UID && uid != 0" in source


def test_release_never_starts_a_server(monkeypatch):
    class Link:
        serial = "R5CX"

        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def host(self, *argv: str, timeout: float = 30.0) -> str:
            self.calls.append(argv)
            return ""  # aucune redirection : aucun serveur lancé

        def shell(self, *argv: str, **kwargs) -> str:
            self.calls.append(argv)
            return ""

    link = Link()
    query.QueryServer(link).release()  # type: ignore[arg-type]
    assert link.calls == [("forward", "--list")]
