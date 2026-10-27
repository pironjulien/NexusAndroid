"""
Cible de fuzzing de NexusAndroid : le masquage commun de tout texte lu sur le téléphone (écran, SMS, notifications,
journaux) et les accords des gestes sortants. Chaque entrée glisse une vraie donnée sensible (carte valide au sens de
Luhn, IBAN à clé juste, code annoncé par un mot-clé, jeton, e-mail) dans un texte quelconque, ou rejoue un accord sur
une action modifiée, et vérifie qu'elle ne ressort jamais.

Lancée sous Atheris par .github/workflows/fuzz.yml (Linux) et, sans Atheris, par tests/test_fuzz_targets.py sur des
octets pseudo-aléatoires (test.ps1, Windows).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from nexus_android import garde_fous, logs, messages  # noqa: E402

_DIGITS = "0123456789"
_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_KEYWORDS = ("code", "Code de vérification", "OTP", "PIN", "verification code", "mot de passe")
_PREFIXES = ("ghp_", "sk-", "glpat-", "AKIA", "xoxb-", "hf_", "npm_")


class Bytes:
    """Lecteur d'octets du fuzzer : entiers, choix et textes tirés des octets, sans jamais échouer (octets épuisés :
    zéros)."""

    def __init__(self, data: bytes) -> None:
        self.data, self.at = data, 0

    def byte(self) -> int:
        if self.at >= len(self.data):
            return 0
        self.at += 1
        return self.data[self.at - 1]

    def pick(self, items: str | tuple[str, ...]) -> str:
        return items[self.byte() % len(items)]

    def chars(self, alphabet: str, size: int) -> str:
        return "".join(self.pick(alphabet) for _ in range(size))

    def text(self, limit: int = 40) -> str:
        size = self.byte() % (limit + 1)
        chunk = self.data[self.at : self.at + size]
        self.at += size
        return chunk.decode("utf-8", "replace")


def _luhn_digit(body: str) -> str:
    total = 0
    for i, c in enumerate(reversed(body)):
        d = int(c) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return str(-total % 10)


def _card(b: Bytes) -> str | None:
    """Numéro de carte valide (Luhn), seul ou en groupes de 4 : None quand tous les chiffres sont égaux (non masqué,
    voulu : 4444 4444 4444 4444 n'est pas une carte)."""
    body = b.pick("23456") + b.chars(_DIGITS, 14)
    digits = body + _luhn_digit(body)
    if len(set(digits)) == 1:
        return None
    return " ".join(digits[i : i + 4] for i in range(0, 16, 4)) if b.byte() & 1 else digits


def _iban(b: Bytes) -> str:
    country = b.pick(tuple(sorted(messages.IBAN_LENGTHS)))
    bban = b.chars(_ALNUM, messages.IBAN_LENGTHS[country] - 4)
    number = int("".join(str(int(c, 36)) for c in bban + country + "00"))
    raw = f"{country}{98 - number % 97:02d}{bban}"
    return " ".join(raw[i : i + 4] for i in range(0, len(raw), 4)) if b.byte() & 1 else raw


def _code(b: Bytes) -> tuple[str, str]:
    code = b.pick("123456789") + b.chars(_DIGITS, 5)
    return code, f"{b.pick(_KEYWORDS)} : {code}"


def mask(data: bytes) -> None:
    """Une carte, un IBAN, un code annoncé, un jeton ou un e-mail entouré d'un texte quelconque ne ressort jamais."""
    b = Bytes(data)
    kind = b.byte() % 5
    before, after = b.text(), b.text()
    if kind == 0:
        secret = _card(b)
        if secret is None:
            return
        shown = secret
    elif kind == 1:
        secret = shown = _iban(b)
    elif kind == 2:
        secret, shown = _code(b)
    elif kind == 3:
        secret = shown = b.pick(_PREFIXES) + b.chars(_ALNUM + "abcdefghijklmnopqrstuvwxyz", 24 + b.byte() % 16)
    else:
        secret = shown = f"{b.chars('abcdefgh123', 1 + b.byte() % 10)}@{b.chars('abcdefgh', 2 + b.byte() % 8)}.fr"
    while secret in before or secret in after:  # seule la donnée insérée est jugée, pas une copie collée autour
        before, after = before.replace(secret, ""), after.replace(secret, "")
    text = f"{before} | {shown} | {after}"  # « | » : un code suivi de « $ » ou « € » est un montant, voulu
    out = logs.mask(text) if kind >= 3 or b.byte() & 1 else messages.mask_codes(text)
    if secret in out:
        raise AssertionError(f"not masked: {secret!r} in {out!r} (from {text!r})")


def accord(data: bytes) -> None:
    """Un accord ne vaut que pour l'action exacte qu'il a liée, et une seule fois."""
    b = Bytes(data)
    fields = [b.text(12) for _ in range(6)]

    def act(values: list[str]) -> dict:
        # context porte l'identité de l'écran (policy._bound) : elle compte comme les autres champs
        return garde_fous.action("android", values[0], values[1], [values[2]], values[3], [values[4]], values[5])

    approved = act(fields)
    changed = list(fields)
    if b.byte() & 1:
        changed[b.byte() % 6] = b.text(12)
    attempted = act(changed)
    accords = garde_fous.Accords(ttl=600)
    token = accords.propose(approved)
    verdict = accords.check(attempted, True, token)
    if (verdict == "") != (attempted == approved):
        raise AssertionError(f"accord for {approved!r} gave {verdict[:80]!r} on {attempted!r}")
    if accords.check(approved, True, token) == "":
        raise AssertionError("accord used twice")


def target(data: bytes) -> None:
    (mask, accord)[data[0] % 2 if data else 0](data[1:])
