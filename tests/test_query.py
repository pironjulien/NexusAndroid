"""Protocole du serveur de lectures du téléphone (phone/NexusQuery.java), sans téléphone."""

import struct

import pytest

from nexus_android import query


def test_encode_matches_the_java_reader():
    data = query.encode([["query", "--uri", "content://sms", "--where", "address='Émile'"]])
    assert data[:8] == b"NXQ1" + struct.pack(">i", 1)
    assert struct.unpack(">i", data[8:12])[0] == 5
    assert "address='Émile'".encode() in data


def test_decode_keeps_order_and_utf8():
    body = "Row: 0 body=Café\n".encode()
    data = b"NXQ1" + struct.pack(">i", len(body)) + body + struct.pack(">i", 0)
    assert query.decode(data, 2) == ["Row: 0 body=Café\n", ""]


@pytest.mark.parametrize("data", [b"", b"NXQ1", b"NXQ1" + struct.pack(">i", 10) + b"court", b"HTTP"])
def test_decode_rejects_missing_or_truncated_answers(data):
    with pytest.raises(ValueError):
        query.decode(data, 1)


def test_dex_shipped_with_the_package():
    data = query.DEX.read_bytes()
    assert data[:4] == b"dex\n" and b"LNexusQuery;" in data
    assert b"com.android.commands.content.Content" in data


def test_existing_forward_is_reused_for_this_phone_only():
    listing = ("RFTEST00001 tcp:5000 localabstract:nexus-query-abc\n192.0.2.10:4000 tcp:6000 localabstract:nexus-query-abc\n"
               "RFTEST00001 tcp:7000 localabstract:nexus-query-old\n")
    assert query.forwarded(listing, "RFTEST00001", "nexus-query-abc") == ["5000"]
    assert query.forwarded(listing, "192.0.2.10:4000", "nexus-query-abc") == ["6000"]
    assert query.forwarded(listing, "RFTEST00001", "nexus-query-new") == []
