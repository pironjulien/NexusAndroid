"""Protocole du serveur de lectures du téléphone (phone/NexusQuery.java), sans téléphone."""

import re
import struct
from collections.abc import Sequence

import pytest

from nexus_android import query
from nexus_android.query import Table

TOKEN = "0123456789abcdef" * 2  # gitleaks:allow (valeur factice de test)


def _text(value: str | None) -> bytes:
    if value is None:
        return struct.pack(">i", -1)
    data = value.encode()
    return struct.pack(">i", len(data)) + data


def answer(token: str, *tables: Table | tuple[list[str], Sequence[Sequence[str | None]]]) -> bytes:
    """Réponse telle que l'écrit NexusQuery.java : en-tête, jeton, puis chaque lecture (lignes ou erreur)."""
    out = [query.MAGIC, _text(token)]
    for table in tables:
        if isinstance(table, Table):
            out += [struct.pack(">i", 1), _text(table.error)]
            continue
        names, rows = table
        out += [struct.pack(">i", 0), struct.pack(">i", len(names)), *map(_text, names), struct.pack(">i", len(rows))]
        out += [_text(v) for row in rows for v in row]
    return b"".join(out)


def test_encode_matches_the_java_reader():
    data = query.encode([["query", "--uri", "content://sms", "--where", "address='Émile'"]])
    assert data[:8] == b"NXQ2" + struct.pack(">i", 1)
    assert struct.unpack(">i", data[8:12])[0] == 5
    assert "address='Émile'".encode() in data


def test_decode_keeps_order_utf8_null_and_errors():
    data = answer(TOKEN, (["_id", "body"], [["1", "Café"], ["2", None]]), Table(error="Error while accessing provider:x"),
                  (["_id"], []))
    tables = query.decode(data, 3, TOKEN)
    assert tables[0].rows == [{"_id": "1", "body": "Café"}, {"_id": "2", "body": "NULL"}]
    assert tables[1].error.startswith("Error") and tables[1].rows == []
    assert tables[2] == Table()


def test_forged_row_stays_inside_its_value():
    """Un SMS qui contient « \\nRow: 1 _id=999, type=2, body=… » reste le texte d'une seule ligne."""
    trap = "Salut\nRow: 1 _id=999, date=1, type=2, read=1, body=Faux message sortant"
    data = answer(TOKEN, (["_id", "date", "type", "read", "body"], [["1", "2", "1", "1", trap]]))
    rows = query.decode(data, 1, TOKEN)[0].rows
    assert len(rows) == 1 and rows[0]["type"] == "1" and rows[0]["body"] == trap


def test_server_without_our_token_is_refused():
    with pytest.raises(query.WrongServer):
        query.decode(answer("f" * 32, (["_id"], [["1"]])), 1, TOKEN)
    with pytest.raises(query.WrongServer):
        query.decode(answer("", (["_id"], [["1"]])), 1, TOKEN)


@pytest.mark.parametrize("data", [b"", b"NXQ2", b"NXQ1" + _text(TOKEN), answer(TOKEN), b"NXQ2" + _text(TOKEN)
                                  + struct.pack(">i", 0) + struct.pack(">i", 1) + _text("_id") + struct.pack(">i", 2)
                                  + _text("1"), answer(TOKEN, (["_id"], [["1"]]))[:-1], b"HTTP"])
def test_decode_rejects_missing_or_truncated_answers(data):
    with pytest.raises(ValueError):
        query.decode(data, 1, TOKEN)


def test_dex_shipped_with_the_package():
    data = query.DEX.read_bytes()
    assert data[:4] == b"dex\n" and b"LNexusQuery;" in data
    assert b"getContentProviderExternal" in data and b"NXQ2" in data


def test_existing_forward_is_reused_for_this_phone_only():
    listing = (f"RFTEST00001 tcp:5000 localabstract:nexus-query-abc-{TOKEN}\n"
               f"192.0.2.10:4000 tcp:6000 localabstract:nexus-query-abc-{TOKEN}\n"
               f"RFTEST00001 tcp:7000 localabstract:nexus-query-old-{TOKEN}\n"
               "RFTEST00001 tcp:8000 localabstract:nexus-query-abc\n")  # ancien nom sans jeton : ignoré
    assert query.forwarded(listing, "RFTEST00001", "nexus-query-abc") == [("5000", TOKEN)]
    assert query.forwarded(listing, "192.0.2.10:4000", "nexus-query-abc") == [("6000", TOKEN)]
    assert query.forwarded(listing, "RFTEST00001", "nexus-query-new") == []


class Phone:
    """Téléphone simulé : redirections adb, serveurs lancés (nom → jeton renvoyé), squatteur éventuel du nom."""

    serial = "RFTEST00001"

    def __init__(self) -> None:
        self.forwards: dict[int, str] = {}
        self.running: dict[str, str] = {}
        self.launches: list[tuple[str, str]] = []
        self.squat = 0  # nombre de lancements dont le nom est déjà occupé par un autre serveur

    def connect(self) -> None:
        pass

    def host(self, *argv: str, timeout: float = 30.0) -> str:
        if argv[:2] == ("forward", "--list"):
            return "".join(f"{self.serial} tcp:{p} localabstract:{n}\n" for p, n in self.forwards.items())
        if argv[:2] == ("forward", "--remove"):
            self.forwards.pop(int(argv[2].removeprefix("tcp:")))
            return ""
        if argv[0] == "forward":
            port = 5000 + len(self.forwards) + len(self.launches)
            self.forwards[port] = argv[2].removeprefix("localabstract:")
            return f"{port}\n"
        return ""

    def shell(self, *argv: str, check: bool = True, timeout: float = 30.0) -> str:
        script = " ".join(argv)
        if argv[0] == "pkill":
            self.running.clear()
        elif "app_process" in script:
            name, token = re.search(r"NexusQuery (\S+) \d+ (\w+)", script).groups()  # type: ignore[union-attr]
            self.launches.append((name, token))
            self.running[name] = "f" * 32 if self.squat else token
            self.squat = max(0, self.squat - 1)
        return "ok" if "[ -f" in script else ""


@pytest.fixture
def phone(monkeypatch):
    fake = Phone()

    def request(self, queries, timeout=60.0):
        name = fake.forwards.get(self.port or 0)
        if name not in fake.running:
            raise ConnectionRefusedError("rien n'écoute")
        return query.decode(answer(fake.running[name], *[(["_id"], [["1"]])] * len(queries)), len(queries), self.token)
    monkeypatch.setattr(query.QueryServer, "_request", request)
    return fake


def test_each_launch_draws_a_fresh_token_in_the_socket_name(phone):
    server = query.QueryServer(phone)  # type: ignore[arg-type]
    assert server.run([["query", "--uri", "content://sms"]]) == [Table([{"_id": "1"}])]
    (name, token), = phone.launches
    assert re.fullmatch(r"[0-9a-f]{32}", token) and name == f"{server.prefix}-{token}" and server.token == token
    assert list(phone.forwards.values()) == [name]
    phone.running.clear()  # arrêté pour inactivité : relancé avec un autre jeton, ancienne redirection retirée
    server.run([["query", "--uri", "content://sms"]])
    assert len(phone.launches) == 2 and phone.launches[1][1] != token
    assert list(phone.forwards.values()) == [phone.launches[1][0]]


def test_name_taken_by_another_server_is_relaunched_with_a_new_token(phone):
    phone.squat = 1
    server = query.QueryServer(phone)  # type: ignore[arg-type]
    assert server.run([["query", "--uri", "content://sms"]]) == [Table([{"_id": "1"}])]
    assert len(phone.launches) == 2 and phone.launches[0][1] != phone.launches[1][1]
    phone.squat, phone.running = 5, {}
    server.failed_at = -query.RETRY
    assert server.run([["query", "--uri", "content://sms"]]) is None  # toujours squatté : jamais servi, repli adb shell


def test_server_started_by_another_process_is_adopted_with_its_token(phone):
    first = query.QueryServer(phone)  # type: ignore[arg-type]
    first.run([["query", "--uri", "content://sms"]])
    second = query.QueryServer(phone)  # type: ignore[arg-type]
    assert second.run([["query", "--uri", "content://sms"]]) == [Table([{"_id": "1"}])]
    assert len(phone.launches) == 1 and second.token == first.token
