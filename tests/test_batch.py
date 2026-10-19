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
    lines = Messages(adb).conversations(15)  # type: ignore[arg-type]
    assert adb.shells == 2 and "Alice (+33612345678)" in lines[0] and "NON LU" in lines[0]
    assert "482913" not in lines[0] and "[code masqué]" in lines[0]
    Messages(adb).conversations(15)  # type: ignore[arg-type]
    assert adb.shells == 3  # noms en cache, partagé entre sessions : une seule vague
