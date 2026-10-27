"""Journal du démon : la trace d'un client parti sans fermer (ConnectionResetError dans _call_connection_lost) n'est
plus écrite ; toute autre erreur d'asyncio l'est toujours."""

from __future__ import annotations

import asyncio
import logging

import pytest

from nexus_android import server


def _report(exc: BaseException, callback: str) -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.default_exception_handler({"message": f"Exception in callback {callback}", "exception": exc})
    finally:
        loop.close()


def test_client_gone_is_not_logged_but_other_errors_are(caplog: pytest.LogCaptureFixture):
    server.quiet_client_resets()
    server.quiet_client_resets()  # une seule fois, même appelé deux fois
    assert sum(isinstance(f, server._ClientGone) for f in logging.getLogger("asyncio").filters) == 1
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        _report(ConnectionResetError(10054, "reset"), "_ProactorBasePipeTransport._call_connection_lost()")
        _report(ConnectionResetError(10054, "reset"), "other_callback()")
        _report(ValueError("boom"), "_ProactorBasePipeTransport._call_connection_lost()")
    assert [r.exc_info[1].__class__.__name__ for r in caplog.records if r.exc_info] == ["ConnectionResetError", "ValueError"]
