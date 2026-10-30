"""Parité indépendante Python/C# sur contrats MCP et transports locaux injectés.

Le binaire de production sert seulement initialize/tools/list. Les appels d'outils
passent exclusivement par NativeContracts --fixture-json, jamais par un téléphone.
Ces tests ne qualifient donc pas les gestes sur un appareil réel.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from nexus_android import extras, server

ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TOOL_NAMES = (
    "act", "apps", "capture", "checkup", "clipboard", "draft", "files", "functions", "info",
    "launch", "logs", "look", "map", "messages", "notifications", "phone", "settings", "web",
)
CONTRACT_FIELDS = ("name", "inputSchema", "outputSchema", "annotations")


def _source_fingerprint() -> str:
    """Include embedded resources and project metadata, not an existing candidate."""
    files = {
        path for folder in (ROOT / "native", ROOT / "tests" / "native") for path in folder.rglob("*")
        if path.is_file() and path.suffix in {".cs", ".csproj", ".props", ".targets", ".json"}
        and not {"obj", "bin"}.intersection(path.relative_to(folder).parts)
    }
    files.update(path for path in (
        ROOT / "src" / "nexus_android" / "garde-fous.json",
        ROOT / "src" / "nexus_android" / "nexus-query.dex",
        ROOT / "global.json", ROOT / "NuGet.Config", ROOT / "nuget.config",
    ) if path.is_file())
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class NativeBinaries:
    product: Path
    fixtures: Path
    directory: Path
    fingerprint: str

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.pop("NEXUS_AGENT", None)
        env.update({
            # Even accidental production tool dispatch cannot find the user's ADB.
            "NEXUS_ANDROID_ADB": str(self.directory / "no-real-adb.exe"),
            "NEXUS_MCP_TOKEN": "synthetic-differential-token",
            "NEXUS_MCP_TOKEN_FILE": str(self.directory / "no-user-token"),
            "NEXUS_JOURNAL_DIR": str(self.directory / "journal"),
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "DOTNET_NOLOGO": "1",
        })
        return env


def _run(command: list[str], *, env: dict[str, str], input_text: str | None = None,
         timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, input=input_text, capture_output=True, encoding="utf-8", errors="replace",
                            check=False, timeout=timeout, env=env, cwd=ROOT, creationflags=NO_WINDOW)
    assert result.returncode == 0, f"{command[:3]} failed:\n{result.stdout}\n{result.stderr}"
    return result


@pytest.fixture(scope="session")
def native_binaries() -> NativeBinaries:
    """Fresh isolated compilation; never accepts NEXUS_ANDROID_NATIVE_EXE."""
    fingerprint = _source_fingerprint()
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    checkout = hashlib.sha256(str(ROOT).casefold().encode()).hexdigest()[:12]
    directory = local / "NexusAndroid" / "tests" / "differential" / checkout / fingerprint[:20]
    directory.mkdir(parents=True, exist_ok=True)
    props = directory / "isolated.props"
    # DirectoryBuildPropsPath overrides both projects' normal shared obj/bin.
    # MSBuildProjectName keeps the ProjectReference's intermediate files separate.
    props.write_text(
        '<Project><PropertyGroup>'
        '<NexusLocalBuildRoot>$(MSBuildThisFileDirectory)build\\$(MSBuildProjectName)</NexusLocalBuildRoot>'
        '<BaseOutputPath>$(NexusLocalBuildRoot)\\bin\\</BaseOutputPath>'
        '<BaseIntermediateOutputPath>$(NexusLocalBuildRoot)\\obj\\</BaseIntermediateOutputPath>'
        '<MSBuildProjectExtensionsPath>$(BaseIntermediateOutputPath)</MSBuildProjectExtensionsPath>'
        '</PropertyGroup></Project>', encoding="utf-8",
    )
    binaries = NativeBinaries(directory / "product" / "NexusAndroid.exe",
                              directory / "fixtures" / "NativeContracts.exe", directory, fingerprint)
    common = ["-c", "Release", f"-p:DirectoryBuildPropsPath={props}", "--nologo", "-v:q"]
    _run(["dotnet", "publish", str(ROOT / "native" / "NexusAndroid.csproj"), *common,
          "-r", "win-x64", "--self-contained", "false", "-p:PublishSingleFile=true",
          "-o", str(binaries.product.parent)], env=binaries.environment())
    _run(["dotnet", "build", str(ROOT / "tests" / "native" / "NativeContracts.csproj"), *common,
          "-o", str(binaries.fixtures.parent)], env=binaries.environment())
    assert _source_fingerprint() == fingerprint, "Native sources changed during build: rerun against stable sources"
    assert binaries.product.is_file() and binaries.fixtures.is_file(), "Isolated native binaries were not produced"
    return binaries


@pytest.fixture(scope="session")
def python_contracts() -> dict[str, dict[str, Any]]:
    # Listing MCP metadata is local and does not initialize a phone connection.
    tools = asyncio.run(server.mcp.list_tools())
    result = {tool.name: tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in tools}
    assert len(tools) == len(result) == 18
    assert set(result) == set(TOOL_NAMES)
    return result


@pytest.fixture(scope="session")
def native_contracts(native_binaries: NativeBinaries) -> dict[str, dict[str, Any]]:
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "offline-differential", "version": "1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    output = _run([str(native_binaries.product), "--mcp"], env=native_binaries.environment(),
                  input_text="".join(json.dumps(request) + "\n" for request in requests), timeout=20)
    responses = [json.loads(line) for line in output.stdout.splitlines() if line.strip()]
    init = next(response for response in responses if response.get("id") == 1)
    assert init["result"]["protocolVersion"] == "2025-06-18"
    listed = next(response for response in responses if response.get("id") == 2)
    assert "error" not in listed, listed
    tools = listed["result"]["tools"]
    result = {tool["name"]: tool for tool in tools}
    assert len(tools) == len(result) == 18
    assert set(result) == set(TOOL_NAMES)
    return result


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_all_native_mcp_contracts_match_stable_python(
    name: str, python_contracts: dict[str, dict[str, Any]], native_contracts: dict[str, dict[str, Any]],
) -> None:
    """Compare nested enums/defaults/required/$defs and annotations without normalization."""
    reference, actual = python_contracts[name], native_contracts[name]
    assert {key: actual[key] for key in CONTRACT_FIELDS if key in actual} == {
        key: reference[key] for key in CONTRACT_FIELDS if key in reference
    }, f"Public MCP contract differs for {name}"


def _fixture_call(binaries: NativeBinaries, tool: str, args: dict[str, Any],
                  fixture: dict[str, Any]) -> dict[str, Any]:
    request = {"tool": tool, "args": args, "fixture": fixture}
    output = _run([str(binaries.fixtures), "--fixture-json"], env=binaries.environment(),
                  input_text=json.dumps(request, ensure_ascii=False) + "\n", timeout=20)
    lines = [json.loads(line) for line in output.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, output.stdout
    result = lines[0]
    assert "driver_error" not in result, result
    assert "error" not in result["response"], result
    assert not result["response"]["result"].get("isError", False), result
    return result


def _validated(result: dict[str, Any], tool: str, contracts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    response = result["response"]["result"]
    assert "structuredContent" in response, f"{tool} omitted its advertised structuredContent"
    data = response["structuredContent"]
    schema = contracts[tool]["outputSchema"]
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda error: str(error.path))
    assert not errors, "\n".join(f"{tool} {list(error.path)}: {error.message}" for error in errors)
    text = "\n".join(block["text"] for block in response["content"] if block["type"] == "text")
    assert data["text"] == text, "Structured and human-readable output disagree"
    return data


def test_tool_without_output_schema_omits_structured_content(native_binaries: NativeBinaries) -> None:
    """30/10 : `map` renvoyait structuredContent: null, rejeté par Claude Code (objet attendu ou champ absent)."""
    result = _fixture_call(native_binaries, "functions", {}, {"shell": {"cmd app_function list-app-functions": "{}"}})
    assert "structuredContent" not in result["response"]["result"], result["response"]["result"]


def _assert_read_only(result: dict[str, Any]) -> None:
    for command in result["commands"]:
        normalized = command.replace("'", "")
        assert not re.search(r"(?:^|[;\s])(?:input|svc|reboot|rm|mkdir)(?:\s|$)|"
                             r"\b(?:settings put|am start|am force-stop|set_dnd|set-location-enabled)\b|--set\b",
                             normalized), f"Read-only call dispatched mutation: {command}"


@pytest.mark.parametrize(("posture", "expected"), [("CLOSED", "folded"), ("HALF_OPENED", "half-open"), ("OPENED", "open")])
@pytest.mark.parametrize(("locked", "trusted"), [(False, True), (True, True), (True, False)])
def test_phone_typed_status_from_controlled_transport(
    native_binaries: NativeBinaries, python_contracts: dict[str, dict[str, Any]],
    posture: str, expected: str, locked: bool, trusted: bool,
) -> None:
    state = ("mCurrentFocus=Window{fixture u0 com.example.app/.Main}\nmWakefulness=Awake\n"
             f"isKeyguardShowing={str(locked).lower()}\n"
             f"mCommittedState=Optional[DeviceState{{identifier=1, name='{posture}'}}]")
    result = _fixture_call(native_binaries, "phone", {}, {"state": state, "shell": {
        "getprop ro.product.model": "Fixture Fold", "getprop ro.build.version.release": "17",
        "dumpsys trust": f"(current): deviceLocked={0 if trusted else 1}",
        "cat /proc/sys/kernel/random/boot_id": "00000000-0000-0000-0000-000000000001",
    }})
    data = _validated(result, "phone", python_contracts)
    expected_fields = {"model": "Fixture Fold", "android": "17", "serial": "fixture-device", "transport": "usb",
                       "foreground": "com.example.app", "posture": expected, "screen_on": True,
                       "locked": locked, "code_required": locked and not trusted,
                       "kept_awake_until": None, "rebooted": False}
    assert {key: data[key] for key in expected_fields} == expected_fields
    _assert_read_only(result)


class FixtureAdb:
    """Small in-memory oracle transport: unmatched reads return empty, no subprocess."""

    def __init__(self, shell: dict[str, str]) -> None:
        self.values = shell

    def shell(self, *args: str, **_kwargs: Any) -> str:
        command = " ".join(args).replace("'", "")
        return next((value for key, value in self.values.items() if key in command), "")


@pytest.mark.parametrize(("name", "shell"), [
    ("wifi", {"wifi_on": "1"}), ("wifi", {"wifi_on": "0"}),
    ("bluetooth", {"bluetooth_on": "1"}), ("data", {"mobile_data": "0"}),
    ("location", {"location_mode": "3"}), ("location", {"location_mode": "0"}),
    ("nfc", {"dumpsys nfc": "mState = on"}), ("dnd", {"zen_mode": "1"}),
    ("dnd", {"zen_mode": "3"}), ("brightness", {"screen_brightness_mode": "1"}),
    ("brightness", {"screen_brightness_mode": "0", "screen_brightness": "128"}),
    ("volume", {"media_session volume": "volume is 7 in range [2..12]"}),
    # Missing/unknown Android values must retain stable semantics, not invent a value.
    ("wifi", {}), ("location", {}), ("dnd", {"zen_mode": "null"}),
    ("brightness", {}), ("volume", {}),
])
def test_settings_typed_read_matches_python(
    native_binaries: NativeBinaries, python_contracts: dict[str, dict[str, Any]], name: str, shell: dict[str, str],
) -> None:
    reference_text, reference_value = extras.setting(cast(Any, FixtureAdb(shell)), name, None, "usb")
    result = _fixture_call(native_binaries, "settings", {"name": name}, {"shell": shell})
    data = _validated(result, "settings", python_contracts)
    assert data == {"name": name, "value": reference_value, "changed": False, "text": reference_text}
    _assert_read_only(result)


@pytest.mark.parametrize("dump", [
    "7200.00 1000.00\nLocation[gps 48.100000,2.100000 hAcc=5.0 et=+1h59m30s]",
    ("7200.00 1000.00\nLocation[gps 48.100000,2.100000 hAcc=5.0 et=+1h30m0s]\n"
     "Location[network 48.200000,2.200000 hAcc=30.0 et=+1h59m30s]"),
    "7200.00 1000.00\n",
])
def test_info_location_typed_response_matches_python(
    native_binaries: NativeBinaries, python_contracts: dict[str, dict[str, Any]], dump: str,
) -> None:
    shell = {"cat /proc/uptime; dumpsys location": dump}
    text, location = extras.location(cast(Any, FixtureAdb(shell)))
    result = _fixture_call(native_binaries, "info", {"kind": "location"}, {"shell": shell})
    data = _validated(result, "info", python_contracts)
    assert data == {"kind": "location", "text": text, "location": location, "calls": [], "contacts": [], "events": []}
    _assert_read_only(result)


@pytest.mark.parametrize(("call_type", "expected_type"), [
    ("1", "incoming"), ("2", "outgoing"), ("3", "missed"),
    ("4", "voicemail"), ("5", "rejected"), ("6", "blocked"),
])
def test_info_calls_preserves_typed_records_and_contact_resolution(
    native_binaries: NativeBinaries, python_contracts: dict[str, dict[str, Any]], call_type: str, expected_type: str,
) -> None:
    timestamp = 1793269800000
    number = "+15550101001"  # Synthetic fixture only.
    result = _fixture_call(native_binaries, "info", {"kind": "calls", "limit": 1}, {"queries": {
        "content://call_log/calls": [{"date": str(timestamp), "duration": "65", "type": call_type, "number": number}],
        "content://com.android.contacts/phone_lookup/*": [{"display_name": "Fixture Contact"}],
    }})
    data = _validated(result, "info", python_contracts)
    assert data["calls"] == [{"date": datetime.fromtimestamp(timestamp / 1000).isoformat(timespec="seconds"),
                              "type": expected_type, "who": f"Fixture Contact ({number})", "number": number,
                              "duration_s": 65}]
    assert data["kind"] == "calls" and data["location"] is None
    assert data["contacts"] == data["events"] == []
    assert result["query_calls"][0]["sort"] == "date DESC LIMIT 1"
    _assert_read_only(result)


@pytest.mark.parametrize(("requested", "expected_limit"), [(0, 1), (200, 200), (201, 200)])
def test_info_calls_retains_python_limit_range(
    native_binaries: NativeBinaries, python_contracts: dict[str, dict[str, Any]],
    requested: int, expected_limit: int,
) -> None:
    rows = [{"date": str(1793269800000 - index * 60000), "duration": "0", "type": "3", "number": ""}
            for index in range(200)]

    class FixtureCalls:
        def _query(self, *_args: Any, **_kwargs: Any) -> list[dict[str, str]]:
            return rows

        def resolve(self, _addresses: list[str]) -> None:
            pass

    reference_text, reference_rows = extras.calls(cast(Any, FixtureCalls()), expected_limit)
    result = _fixture_call(native_binaries, "info", {"kind": "calls", "limit": requested}, {"queries": {
        "content://call_log/calls": rows,
    }})
    data = _validated(result, "info", python_contracts)
    assert data["calls"] == reference_rows
    assert data["text"] == reference_text
    assert result["query_calls"][0]["sort"] == f"date DESC LIMIT {expected_limit}"
    _assert_read_only(result)


def test_info_contacts_returns_names_numbers_and_deduplicates(
    native_binaries: NativeBinaries, python_contracts: dict[str, dict[str, Any]],
) -> None:
    result = _fixture_call(native_binaries, "info", {"kind": "contacts", "query": "Fixture Contact"}, {"queries": {
        "content://com.android.contacts/data/phones/filter/*": [
            {"display_name": "Fixture Contact", "data1": "+15550101001"},
            {"display_name": "Fixture Contact", "data1": "+1 555 010 1001"},
            {"display_name": "Fixture Contact", "data1": "+15550101002"},
        ],
    }})
    data = _validated(result, "info", python_contracts)
    assert data["contacts"] == [{"name": "Fixture Contact", "number": "+15550101001"},
                                {"name": "Fixture Contact", "number": "+15550101002"}]
    assert data["kind"] == "contacts" and data["location"] is None
    assert data["calls"] == data["events"] == []
    _assert_read_only(result)


def test_info_calendar_preserves_recurring_instances_nulls_and_all_day(
    native_binaries: NativeBinaries, python_contracts: dict[str, dict[str, Any]],
) -> None:
    begin, end = 1793269800000, 1793273400000
    result = _fixture_call(native_binaries, "info", {"kind": "calendar", "days": 3}, {"queries": {
        "content://com.android.calendar/instances/when/*": [
            {"begin": str(begin), "end": str(end), "allDay": "0", "eventLocation": "Fixture Room", "title": "Recurring Fixture"},
            {"begin": str(begin + 86400000), "end": "NULL", "allDay": "1", "eventLocation": "NULL", "title": "Recurring Fixture"},
        ],
    }})
    data = _validated(result, "info", python_contracts)
    assert data["events"] == [
        {"begin": datetime.fromtimestamp(begin / 1000).isoformat(timespec="minutes"),
         "end": datetime.fromtimestamp(end / 1000).isoformat(timespec="minutes"),
         "all_day": False, "title": "Recurring Fixture", "location": "Fixture Room"},
        {"begin": datetime.fromtimestamp((begin + 86400000) / 1000).isoformat(timespec="minutes"),
         "end": None, "all_day": True, "title": "Recurring Fixture", "location": ""},
    ]
    assert data["kind"] == "calendar" and data["location"] is None
    assert data["calls"] == data["contacts"] == []
    query = next(call for call in result["query_calls"] if "/instances/when/" in call["uri"])
    query_begin, query_end = map(int, query["uri"].rsplit("/", 2)[1:])
    assert query_end - query_begin == 3 * 86400000
    assert query["sort"] == "begin ASC"
    _assert_read_only(result)
