"""Lectures groupées des messages, sans téléphone : requêtes lancées en parallèle dans un seul adb shell."""

import re

from nexus_android import messages as m
from nexus_android.messages import Messages, chained, parse_rows, query_command, split_batch, split_parts


def test_batch_split_keeps_order_and_ignores_forged_markers():
    out = ("Row: 0 _id=1, body=Salut\n@@zz99 1\nfaux séparateur\n"
           "\n@@ab12 1\nNo result found.\n\n@@ab12 2\n")
    chunks = split_batch("\n@@ab12 0\n" + out, "ab12", 3)
    assert parse_rows(chunks[0], "body")[0]["body"] == "Salut\n@@zz99 1\nfaux séparateur"
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
    """Téléphone simulé : répond à chaque commande d'un lot selon son URI, compte les adb shell."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers, self.shells = answers, 0

    def shell(self, *argv: str, **_: object) -> str:
        self.shells += 1
        script = argv[-1]
        token = re.search(r"nexus-q-(\w+)", script).group(1)  # type: ignore[union-attr]
        commands = [line for line in script.splitlines() if line.startswith("( ")]
        out = ""
        for i, command in enumerate(commands):
            body = next((v for k, v in self.answers.items() if k in command), "No result found.\n")
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
    assert adb.shells == 2 and "Alice (+33612345678)" in lines[0] and "NON LU" in lines[0]
    assert "482913" not in lines[0] and "[code masqué]" in lines[0]
    Messages(adb, fast=False).conversations(15)  # type: ignore[arg-type]
    assert adb.shells == 3  # noms en cache, partagé entre sessions : une seule vague


class FakeServer:
    """Serveur du téléphone simulé : répond selon l'URI de chaque requête, compte les échanges."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.calls: list[list[list[str]]] = []

    def run(self, queries: list[list[str]]) -> list[str] | None:
        self.calls.append(queries)
        return [next((v for k, v in self.answers.items() if k in " ".join(q)), "No result found.\n") for q in queries]


def test_served_reads_chain_in_two_exchanges_and_match_shell_output():
    server = FakeServer({"content://mms/part": "Row: 0 _id=5, mid=12, ct=text/plain, text=Salut\n",
                         "content://mms --projection _id": "Row: 0 _id=12\nRow: 1 _id=11\n",
                         "content://sms": "Row: 0 _id=1, body=a\n"})
    messages = Messages(FakeAdb({}), fast=False)  # type: ignore[arg-type]
    messages.fast = server  # type: ignore[assignment]
    sms, part = messages._batch([m.query_args("content://sms", ["_id", "body"]),
                                 m.Chain(m.query_args("content://mms", ["_id"]), "content://mms/part",
                                         ["_id", "mid", "ct", "text"], "mid")])
    assert sms == "Row: 0 _id=1, body=a\n" and "text=Salut" in part
    assert len(server.calls) == 2 and server.calls[1][0][-2:] == ["--where", "mid IN (12,11)"]


def test_chain_with_no_ids_gives_empty_output_like_the_shell():
    chain = m.Chain(m.query_args("content://mms", ["_id"]), "content://mms/addr", ["msg_id", "address"], "msg_id")
    assert chain.second("No result found.\n") is None
    assert chain.second("Error while accessing provider:mms\n") is None


def test_served_falls_back_to_shell_when_server_missing():
    class Missing:
        def run(self, queries: list[list[str]]) -> None:
            return None

    adb = FakeAdb({"content://sms": "Row: 0 _id=1, body=a\n"})
    messages = Messages(adb, fast=False)  # type: ignore[arg-type]
    messages.fast = Missing()  # type: ignore[assignment]
    assert messages._batch([m.query_args("content://sms", ["_id", "body"])]) == ["Row: 0 _id=1, body=a\n"]
    assert adb.shells == 1
