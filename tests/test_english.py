"""Ce que reçoit le modèle est en anglais et neutre : descriptions des outils et de leurs paramètres, réponses d'erreur
et de refus. Aucun vrai téléphone : adb simulé (test_guards, test_robustness)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from test_guards import field, node, ui
from test_robustness import phone, wifi  # noqa: F401  (fixture phone)

from nexus_android import adb as adb_mod
from nexus_android import extras, server
from nexus_android.adb import PhoneError
from nexus_android.server import Step

FRENCH_WORDS = ("les", "des", "une", "est", "sont", "avec", "sans", "pour", "dans", "jamais", "seulement", "puis",
                "aucun", "aucune", "sinon", "déjà", "téléphone", "écran", "geste", "vous", "fichier", "introuvable",
                "appli", "cette", "aussi")
FRENCH = re.compile(r"[àâçéèêëîïôöûùü«»]|\b(?:" + "|".join(FRENCH_WORDS) + r")\b", re.I)
STEERING = re.compile(r"prefer|instead of|rather than|better than|plutôt que|claude in chrome", re.I)


def french(text: str) -> list[str]:
    return [m.group(0) for m in FRENCH.finditer(text)]


def _schema_texts(schema: Any) -> list[str]:
    """Descriptions et titres d'un schéma JSON, à toute profondeur ($defs compris)."""
    found: list[str] = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in ("description", "title") and isinstance(value, str):
                found.append(value)
            else:
                found += _schema_texts(value)
    elif isinstance(schema, list):
        for item in schema:
            found += _schema_texts(item)
    return found


def _tools() -> list[Any]:
    return asyncio.run(server.mcp.list_tools())


def _tool_texts() -> dict[str, list[str]]:
    texts: dict[str, list[str]] = {}
    for tool in _tools():
        title = tool.annotations.title if tool.annotations else None
        texts[tool.name] = [tool.description or "", tool.title or "", title or "",
                            *_schema_texts(tool.input_schema), *_schema_texts(tool.output_schema or {})]
    return texts


# ---------------------------------------------------------------- (a) descriptions, titres, schémas


def test_tool_descriptions_and_schemas_are_english():
    texts = _tool_texts()
    assert texts
    for name, values in texts.items():
        for value in values:
            assert not french(value), (name, french(value), value)


def test_tool_descriptions_fit_the_limit():
    for tool in _tools():
        assert tool.description and len(tool.description) <= 2048, tool.name


# ---------------------------------------------------------------- (c) aucune comparaison avec d'autres outils


def test_no_steering_in_instructions_and_descriptions():
    instructions = server.mcp.instructions or ""
    assert instructions and not STEERING.search(instructions), STEERING.search(instructions)
    assert "in French" not in instructions
    for name, values in _tool_texts().items():
        for value in values:
            assert not STEERING.search(value), (name, value)


# ---------------------------------------------------------------- (b) erreurs et refus hors ligne


def act(*steps: Step) -> str:
    return server._act(list(steps), "none")[0]


def test_absent_phone_errors_are_english(monkeypatch, tmp_path):
    wifi(monkeypatch, tmp_path)
    link = adb_mod.Adb()
    for _ in range(2):  # première recherche, puis échec gardé sans nouvelle recherche
        with pytest.raises(PhoneError) as error:
            link.connect()
        assert str(error.value) and not french(str(error.value)), str(error.value)


def test_step_reports_are_english(phone):  # noqa: F811
    phone.uis = [ui(node("Pay now", (0, 0, 500, 200)), field())]
    reports = [
        act(Step(do="tap", target="Missing label")),  # cible introuvable
        act(Step(do="tap", target="Pay now")),  # confirm=true exigé
        act(Step(do="key", keys="enter")),  # Entrée : confirm=true exigé
        act(Step(do="swipe", target="10,10")),  # argument manquant
        act(Step(do="key")),  # touche manquante
        act(Step(do="tap", target="[999]")),  # numéro inconnu
    ]
    assert reports[1].startswith("✗") and "confirm=true" in reports[1]
    assert reports[2].startswith("✗") and "confirm=true" in reports[2]
    phone.uis = [ui(field(password=True))]
    reports.append(act(Step(do="type", text="secret")))  # champ de mot de passe
    phone.lock = "mIsShowing=true"
    reports.append(act(Step(do="tap", target="0,1750", confirm=True)))  # téléphone verrouillé
    phone.lock = "nothing readable"
    reports.append(act(Step(do="tap", target="0,1750")))  # verrouillage illisible
    assert phone.inputs == []
    for report in reports:
        assert report.startswith("✗") and not french(report), report


def test_invalid_setting_value_is_english():
    class Settings:
        def shell(self, *argv, **kw):
            return {"screen_brightness_mode": "0", "screen_brightness": "50"}.get(argv[-1], "")

    with pytest.raises(PhoneError) as error:
        extras.setting(Settings(), "brightness", "strong", "usb")  # type: ignore[arg-type]
    assert "1-100" in str(error.value) and not french(str(error.value)), str(error.value)
