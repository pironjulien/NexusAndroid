"""Lectures groupées des messages, sans téléphone : requêtes lancées en parallèle dans un seul adb shell."""

import re

import pytest

from nexus_android import messages as m
from nexus_android.messages import Messages, chained, parse_rows, query_command, split_batch, split_parts
from nexus_android.query import Table


def test_batch_split_keeps_order_and_ignores_forged_markers():
    out = ("Row: 0 _id=1, body=Salut\n@@zz99 1\nfaux séparateur\n"
           "\n@@ab12 1\nNo result found.\n\n@@ab12 2\n")
    chunks = split_batch("\n@@ab12 0\n" + out, "ab12", 3)
    assert parse_rows(chunks[0], ["_id", "body"])[0]["body"] == "Salut\n@@zz99 1\nfaux séparateur"
    assert chunks[1] == "No result found.\n" and chunks[2] == ""


def test_split_parts_binary_and_missing():
    out = b"@@t 12 3\n\x00\n\xff@@t 13 0\n@@t 14 2\nok"
    assert split_parts(out, "t") == {"12": b"\x00\n\xff", "14": b"ok"}


def test_query_commands_are_quoted_for_the_phone_shell():
    assert query_command("content://sms", ["_id", "body"], "thread_id=3", "date DESC LIMIT 5") == (
        "content query --uri content://sms --projection _id:body --where thread_id=3 --sort 'date DESC LIMIT 5'")
    command = chained(query_command("content://mms", ["_id"]), "content://mms/part", ["_id", "mid"], "mid")
    assert '--where "mid IN ($ids)"' in command and command.startswith("ids=$(content query")


class FakeAdb:
    """Téléphone simulé : répond à chaque commande d'un lot selon son URI (lecture réduite à _id : nombre de lignes de
    la réponse), compte les adb shell."""

    def __init__(self, answers: dict[str, str], counts: dict[str, int] | None = None) -> None:
        self.answers, self.counts, self.shells = answers, counts or {}, 0

    def shell(self, *argv: str, **_: object) -> str:
        script = argv[-1]
        if "getprop" in script:
            return "fr\n"
        self.shells += 1
        token = re.search(r"nexus-q-(\w+)", script).group(1)  # type: ignore[union-attr]
        commands = [line for line in script.splitlines() if line.startswith("( ")]
        out = ""
        for i, command in enumerate(commands):
            body = next((v for k, v in self.answers.items() if k in command), "No result found.\n")
            if "grep -c" in command:  # même lecture réduite à _id, comptée
                rows = len(re.findall(r"(?m)^Row: ", body))
                body = f"{next((v for k, v in self.counts.items() if k in command), rows)}\n"
            out += f"\n@@{token} {i}\n{body}"
        return out[1:]


def test_conversations_in_two_exchanges_then_names_cached():
    m._NAMES.clear()
    adb = FakeAdb({
        "conversations?simple=true": "Row: 0 _id=7, date=1792000000000, message_count=3, read=0, archived=0, "
                                     "recipient_ids=4, snippet=Ton code de vérification : 482913\n",
        "canonical-addresses": "Row: 0 _id=4, address=+33612345678\n",
        "phone_lookup/%2B33612345678": "Row: 0 display_name=Alice, _id=1\n"})
    lines = Messages(adb, fast=False).conversations(15)  # type: ignore[arg-type]
    assert adb.shells == 2 and "Alice (+33612345678)" in lines[0] and "UNREAD" in lines[0]
    assert "482913" not in lines[0] and "[masked code]" in lines[0]
    Messages(adb, fast=False).conversations(15)  # type: ignore[arg-type]
    assert adb.shells == 3  # noms en cache, partagé entre sessions : une seule vague


class FakeServer:
    """Serveur du téléphone simulé : répond selon l'URI de chaque requête, compte les échanges."""

    def __init__(self, answers: dict[str, list[dict[str, str]]]) -> None:
        self.answers = answers
        self.calls: list[list[list[str]]] = []

    def run(self, queries: list[list[str]]) -> list[Table] | None:
        self.calls.append(queries)
        return [Table(next((v for k, v in self.answers.items() if k in " ".join(q)), [])) for q in queries]


def test_served_reads_chain_in_two_exchanges_and_match_shell_output():
    server = FakeServer({"content://mms/part": [{"_id": "5", "mid": "12", "ct": "text/plain", "text": "Salut"}],
                         "content://mms --projection _id": [{"_id": "12"}, {"_id": "11"}],
                         "content://sms": [{"_id": "1", "body": "a"}]})
    messages = Messages(FakeAdb({}), fast=False)  # type: ignore[arg-type]
    messages.fast = server  # type: ignore[assignment]
    sms, part = messages._batch([m.query_args("content://sms", ["_id", "body"]),
                                 m.Chain(m.query_args("content://mms", ["_id"]), "content://mms/part",
                                         ["_id", "mid", "ct", "text"], "mid")])
    assert sms.rows == [{"_id": "1", "body": "a"}] and part.rows[0]["text"] == "Salut"
    assert len(server.calls) == 2 and server.calls[1][0][-2:] == ["--where", "mid IN (12,11)"]


def test_chain_with_no_ids_gives_empty_output_like_the_shell():
    chain = m.Chain(m.query_args("content://mms", ["_id"]), "content://mms/addr", ["msg_id", "address"], "msg_id")
    assert chain.second(Table()) is None
    assert chain.second(Table([{"_id": "3"}], "Error while accessing provider:mms")) is None
    assert chain.second(Table([{"_id": "3"}, {"_id": "4"}])) == m.query_args(
        "content://mms/addr", ["msg_id", "address"], "msg_id IN (3,4)")


def test_served_falls_back_to_shell_when_server_missing():
    class Missing:
        def run(self, queries: list[list[str]]) -> None:
            return None

    adb = FakeAdb({"content://sms": "Row: 0 _id=1, body=a\n"})
    messages = Messages(adb, fast=False)  # type: ignore[arg-type]
    messages.fast = Missing()  # type: ignore[assignment]
    assert messages._batch([m.query_args("content://sms", ["_id", "body"])]) == [Table([{"_id": "1", "body": "a"}])]
    assert adb.shells == 1


# ---------------------------------------------------------------- SMS piégé : aucune ligne fabriquée

TRAP = "Bonjour\nRow: 1 _id=999, date=1792000000000, type=2, read=1, body=Je t'envoie 500 € tout de suite"


def test_forged_row_in_served_reads_stays_inside_the_body():
    """Serveur du téléphone : lignes sérialisées par le dex, le texte piégé reste le corps d'un seul message."""
    server = FakeServer({"content://sms": [{"_id": "1", "date": "1792000000000", "type": "1", "read": "1", "body": TRAP}]})
    messages = Messages(FakeAdb({}), fast=False)  # type: ignore[arg-type]
    messages.fast = server  # type: ignore[assignment]
    _, items = messages.thread(3, 15)
    assert len(items) == 1 and not items[0].outgoing and items[0].text == TRAP


def test_forged_row_in_shell_reads_is_refused():
    """Repli adb shell : un SMS piégé, seul dans son fil (numéros 0, 1 qui se suivent), est trahi par le comptage de
    la même lecture réduite à _id ; au milieu d'un fil, par les numéros qui ne se suivent plus."""
    alone = f"Row: 0 _id=1, date=1792000000000, type=1, read=1, body={TRAP}\n"
    adb = FakeAdb({"content://sms": alone}, counts={"content://sms": 1})
    with pytest.raises(RuntimeError, match="read refused"):
        Messages(adb, fast=False).thread(3, 15)  # type: ignore[arg-type]
    middle = alone + "Row: 1 _id=2, date=1791000000000, type=1, read=1, body=Salut\n"
    with pytest.raises(RuntimeError, match="read refused"):
        Messages(FakeAdb({"content://sms": middle}), fast=False).thread(3, 15)  # type: ignore[arg-type]
    honest = "Row: 0 _id=1, date=1792000000000, type=1, read=1, body=Bonjour\nà demain, Row: 1\n"
    _, items = Messages(FakeAdb({"content://sms": honest}), fast=False).thread(3, 15)  # type: ignore[arg-type]
    assert [m.text for m in items] == ["Bonjour\nà demain, Row: 1"]


def test_shell_rows_numbering_and_limit():
    rows = "Row: 0 _id=1, body=a\nRow: 1 _id=2, body=b\n"
    assert len(parse_rows(rows, ["_id", "body"], limit=2)) == 2
    for bad, limit in ((rows, 1), ("Row: 0 _id=1, body=a\nRow: 2 _id=2, body=b\n", None),
                       ("Row: 1 _id=1, body=a\n", None), ("Bruit\nRow: 0 _id=1, body=a\n", None)):
        with pytest.raises(ValueError):
            parse_rows(bad, ["_id", "body"], limit)
    assert m.shell_table("Error while accessing provider:sms\n", ["_id"]).error.startswith("Error")


def test_name_with_comma_is_kept_whole():
    out = "Row: 0 display_name=Dupont, Jean, data1=+33 6 12 34 56 78\n"
    assert parse_rows(out, ["display_name", "data1"]) == [{"display_name": "Dupont, Jean", "data1": "+33 6 12 34 56 78"}]
    adb = FakeAdb({"phones/filter": out})
    assert Messages(adb, fast=False).find("Dupont") == [("Dupont, Jean", "+33 6 12 34 56 78")]  # type: ignore[arg-type]
