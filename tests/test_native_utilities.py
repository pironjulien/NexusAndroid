"""Utilitaires C# sur transports injectés : jamais d'ADB ni de téléphone réel.

Le pilote et la compilation isolée sont partagés avec la qualification différentielle,
mais les assertions fonctionnelles ci-dessous couvrent les utilitaires séparément.
"""

from __future__ import annotations

import base64
import io
import json
import re
import subprocess
from typing import Any

import pytest
from PIL import Image, ImageOps
from test_native_differential import NativeBinaries
from test_native_differential import native_binaries as native_binaries


def call(driver: NativeBinaries, tool: str, args: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    request = {"tool": tool, "args": args, "fixture": fixture}
    result = subprocess.run(
        [str(driver.fixtures), "--fixture-json"], input=json.dumps(request) + "\n",
        capture_output=True, encoding="utf-8", errors="replace", check=False, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=driver.environment(),
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert "driver_error" not in output, output
    return output


def text(result: dict[str, Any]) -> str:
    return "\n".join(block.get("text", "") for block in result["response"]["result"].get("content", []))


def success(result: dict[str, Any]) -> str:
    assert not result["response"]["result"].get("isError"), result
    return text(result)


THREAD_URI = "content://mms-sms/conversations?simple=true"
CANONICAL_URI = "content://mms-sms/canonical-addresses"


def conversations() -> dict[str, Any]:
    return {"shell": {"getprop gsm.sim.operator.iso-country": "fr\n"}, "queries": {
        CANONICAL_URI: [{"_id": "1", "address": "+33612345678"}, {"_id": "2", "address": "+33699999999"}],
        THREAD_URI: [
            {"_id": "7", "date": "2001", "snippet": "new", "recipient_ids": "1", "message_count": "3", "read": "0"},
            {"_id": "8", "date": "1001", "snippet": "old", "recipient_ids": "2", "message_count": "1", "read": "1", "archived": "1"},
            {"_id": "9", "date": "3001", "snippet": "empty draft", "recipient_ids": "1", "message_count": "0"},
        ],
    }}


def test_conversation_page_before_filters_before_limit_and_keeps_cursor(native_binaries: NativeBinaries) -> None:
    result = call(native_binaries, "messages", {"limit": 1, "before": "2001"}, conversations())
    body = success(result)
    assert "[8]" in body and "[7]" not in body and "[9]" not in body
    assert "hidden in the app" in body and "messages(before=" in body
    query = next(row for row in result["query_calls"] if row["uri"] == THREAD_URI)
    assert "message_count>0 AND date<2001" == query["where"]


def test_contact_uses_latest_one_to_one_thread_and_normalizes_sim_numbers(native_binaries: NativeBinaries) -> None:
    fixture = conversations()
    fixture["queries"]["content://com.android.contacts/data/phones/filter/Alice"] = [
        {"display_name": "Alice", "data1": "06 12 34 56 78"},
        {"display_name": "Alice", "data1": "+33 6 12 34 56 78"},
    ]
    fixture["queries"][THREAD_URI].append({"_id": "10", "date": "4001", "recipient_ids": "1 2", "message_count": "4"})
    fixture["queries"]["content://sms"] = [{"_id": "1", "thread_id": "7", "date": "2000", "type": "1", "read": "0", "body": "hello Alice"}]
    body = success(call(native_binaries, "messages", {"contact": "Alice", "images": 0}, fixture))
    assert "Conversation [7]" in body and "hello Alice" in body
    assert "Conversation [10]" not in body


def test_contact_ambiguity_is_refused(native_binaries: NativeBinaries) -> None:
    fixture = conversations()
    fixture["queries"]["content://com.android.contacts/data/phones/filter/Al"] = [
        {"display_name": "Alice", "data1": "0612345678"}, {"display_name": "Albert", "data1": "0699999999"},
    ]
    result = call(native_binaries, "messages", {"contact": "Al"}, fixture)
    assert result["response"]["result"]["isError"] and "Several contacts" in text(result)


@pytest.mark.parametrize("value", ["not a date", "2026-99-90", "10/12/2026"])
def test_message_invalid_cursor_reads_no_message_tables(native_binaries: NativeBinaries, value: str) -> None:
    result = call(native_binaries, "messages", {"before": value}, {})
    assert result["response"]["result"]["isError"] and "ISO date" in text(result)
    assert not result["query_calls"]


def test_thread_merge_boundary_attachment_mentions_and_chronological_order(native_binaries: NativeBinaries) -> None:
    fixture = conversations()
    fixture["queries"]["content://sms"] = [
        {"_id": "1", "thread_id": "7", "date": "1001", "body": "old sms", "type": "2", "read": "1"},
        {"_id": "2", "thread_id": "7", "date": "3001", "body": "new sms", "type": "1", "read": "0"},
        {"_id": "3", "thread_id": "7", "date": "5000", "body": "outside boundary", "type": "1"},
    ]
    fixture["queries"]["content://mms"] = [{"_id": "4", "thread_id": "7", "date": "2", "msg_box": "1", "read": "0"}]
    fixture["queries"]["content://mms/part"] = [
        {"_id": "30", "mid": "4", "ct": "text/plain", "text": "middle mms"},
        {"_id": "31", "mid": "4", "ct": "image/png"}, {"_id": "32", "mid": "4", "ct": "video/mp4"},
    ]
    result = call(native_binaries, "messages", {"conversation": 7, "limit": 3, "before": "4001", "images": 0}, fixture)
    body = success(result)
    assert body.index("old sms") < body.index("middle mms") < body.index("new sms")
    assert "outside boundary" not in body and "image not shown" in body and "video not shown" in body
    assert "older: messages(conversation=7" in body
    assert next(r for r in result["query_calls"] if r["uri"] == "content://mms")["where"] == "thread_id=7 AND date<5"
    assert all(block["type"] != "image" for block in result["response"]["result"]["content"])


def test_thread_output_keeps_message_boundaries_and_masks_before_budget(native_binaries: NativeBinaries) -> None:
    fixture = conversations()
    fixture["queries"]["content://sms"] = [
        {"_id": str(index), "thread_id": "7", "date": str(10000 + index), "type": "1", "body": f"message{index}: " + "long text " * 3000}
        for index in range(4)
    ]
    body = success(call(native_binaries, "messages", {"conversation": 7, "limit": 4, "images": 0}, fixture))
    assert len(body) <= 60000 and "message3:" in body and "older:" in body
    assert "message0:" not in body


def image_fixture(data: bytes) -> dict[str, Any]:
    fixture = conversations()
    fixture["queries"]["content://mms"] = [
        {"_id": "2", "thread_id": "7", "date": "2", "msg_box": "1"},
        {"_id": "3", "thread_id": "7", "date": "3", "msg_box": "1"},
    ]
    fixture["queries"]["content://mms/part"] = [
        {"_id": "21", "mid": "2", "ct": "image/jpeg"}, {"_id": "31", "mid": "3", "ct": "image/jpeg"},
    ]
    fixture["exec_out"] = {"content read --uri content://mms/part/": base64.b64encode(data).decode()}
    return fixture


@pytest.mark.parametrize(("limit", "count", "expected"), [(2, 0, 0), (2, 1, 1), (2, 2, 2), (1, 2, 1)])
def test_message_images_read_only_selected_recent_parts(native_binaries: NativeBinaries, limit: int, count: int, expected: int) -> None:
    encoded = io.BytesIO()
    Image.new("RGB", (12, 6), "red").save(encoded, format="PNG")
    result = call(native_binaries, "messages", {"conversation": 7, "images": count, "limit": limit}, image_fixture(encoded.getvalue()))
    success(result)
    reads = [command for command in result["commands"] if command.startswith("content read")]
    images = [block for block in result["response"]["result"]["content"] if block["type"] == "image"]
    assert len(reads) == len(images) == expected
    if expected:
        assert reads[0].endswith("/31") and "[image 1]" in text(result)
    if expected > 1:
        assert reads[1].endswith("/21") and "[image 2]" in text(result)
    for block in images:
        decoded = Image.open(io.BytesIO(base64.b64decode(block["data"])))
        assert decoded.width > 0 and decoded.height > 0


def test_unreadable_message_image_does_not_discard_thread(native_binaries: NativeBinaries) -> None:
    result = call(native_binaries, "messages", {"conversation": 7, "images": 1}, image_fixture(b"not an image"))
    body = success(result)
    assert "[unreadable image]" in body
    assert not any(block["type"] == "image" for block in result["response"]["result"]["content"])


@pytest.mark.parametrize("orientation", [2, 3, 4, 5, 6, 7, 8])
def test_message_photo_applies_exif_orientation(native_binaries: NativeBinaries, orientation: int) -> None:
    picture = Image.new("RGB", (64, 32), "red")
    picture.paste("blue", (32, 0, 64, 16))
    picture.paste("green", (0, 16, 32, 32))
    picture.paste("yellow", (32, 16, 64, 32))
    exif = picture.getexif()
    exif[274] = orientation
    encoded = io.BytesIO()
    picture.save(encoded, format="JPEG", exif=exif, quality=95)
    source = encoded.getvalue()
    expected = ImageOps.exif_transpose(Image.open(io.BytesIO(source))).convert("RGB")
    result = call(native_binaries, "messages", {"conversation": 7, "images": 1, "limit": 1}, image_fixture(source))
    success(result)
    block = next(block for block in result["response"]["result"]["content"] if block["type"] == "image")
    actual = Image.open(io.BytesIO(base64.b64decode(block["data"]))).convert("RGB")
    assert actual.size == expected.size
    for point in [(8, 8), (actual.width - 8, 8), (8, actual.height - 8), (actual.width - 8, actual.height - 8)]:
        actual_pixel, expected_pixel = actual.getpixel(point), expected.getpixel(point)
        assert isinstance(actual_pixel, tuple) and isinstance(expected_pixel, tuple)
        assert max(abs(a - b) for a, b in zip(actual_pixel, expected_pixel, strict=True)) < 5


def test_rcs_group_resolves_real_members_without_owner(native_binaries: NativeBinaries) -> None:
    fixture = conversations()
    group = base64.b32encode(b"\x1f+33612345678\x1fgroup").decode() + "@rcs.google.com"
    fixture["queries"][CANONICAL_URI] = [{"_id": "1", "address": group}]
    fixture["queries"][THREAD_URI] = [{"_id": "7", "recipient_ids": "1", "date": "2000", "message_count": "2"}]
    fixture["queries"]["content://mms"] = [{"_id": "3", "date": "2", "thread_id": "7", "msg_box": "1"}]
    fixture["queries"]["content://mms/addr"] = [
        {"msg_id": "3", "type": "137", "address": "+33699999999"},
        {"msg_id": "3", "type": "151", "address": "+33612345678"},
    ]
    fixture["queries"]["content://mms/part"] = [{"_id": "30", "mid": "3", "ct": "text/plain", "text": "group hello"}]
    body = success(call(native_binaries, "messages", {"conversation": 7, "images": 0}, fixture))
    assert "RCS group" in body and "+33699999999" in body and "+33612345678" not in body


def draft_fixture(package: str = "com.example.sms", body: str = "draft text") -> dict[str, Any]:
    fixture = conversations()
    fixture["shell"].update({"get-role-holders": package, "am start": "Status: ok"})
    fixture["state"] = f"mCurrentFocus=Window{{token u0 {package}/.Main}}\nmWakefulness=Awake\nisKeyguardShowing=false"
    fixture["xml"] = f"<hierarchy><node text='{body}' package='{package}' resource-id='compose' class='android.widget.EditText' bounds='[0,0][200,100]' editable='true' enabled='true'/><node text='Send' package='{package}' resource-id='send_button' class='android.widget.Button' bounds='[200,0][300,100]' clickable='true' enabled='true'/></hierarchy>"
    return fixture


def test_draft_conversation_uses_role_sms_and_returns_real_agreement_without_send(native_binaries: NativeBinaries) -> None:
    result = call(native_binaries, "draft", {"to": "[7]", "text": "draft text"}, draft_fixture())
    body = success(result)
    starts = [command for command in result["commands"] if "'am' 'start'" in command]
    assert len(starts) == 1 and "'smsto:+33612345678'" in starts[0] and "'-p' 'com.example.sms'" in starts[0]
    assert "exact text read back" in body and re.search(r"accord=[a-f0-9]{48}", body)
    assert not any("'input' 'tap'" in command for command in result["commands"])


def test_group_draft_refused_before_launch(native_binaries: NativeBinaries) -> None:
    fixture = draft_fixture()
    fixture["queries"][THREAD_URI][0]["recipient_ids"] = "1 2"
    result = call(native_binaries, "draft", {"to": "[7]", "text": "draft text"}, fixture)
    assert result["response"]["result"]["isError"] and "Group conversation" in text(result)
    assert not any("'am' 'start'" in command for command in result["commands"])


@pytest.mark.parametrize(("country", "number", "expected"), [
    ("fr", "0612345678", "33612345678"), ("be", "0470123456", "32470123456"),
    ("it", "0612345678", "390612345678"), ("hu", "0612345678", "3612345678"),
    ("", "+447123456789", "447123456789"), ("", "0033612345678", "33612345678"),
])
def test_whatsapp_uses_sim_metadata_not_guessed_country(native_binaries: NativeBinaries, country: str, number: str, expected: str) -> None:
    fixture = draft_fixture("com.whatsapp")
    fixture["shell"]["getprop gsm.sim.operator.iso-country"] = country
    result = call(native_binaries, "draft", {"to": number, "text": "draft text", "app": "whatsapp"}, fixture)
    success(result)
    assert any(f"https://wa.me/{expected}?text=draft%20text" in command for command in result["commands"])


def test_whatsapp_unknown_sim_national_number_refused_before_launch(native_binaries: NativeBinaries) -> None:
    fixture = draft_fixture("com.whatsapp")
    fixture["shell"]["getprop gsm.sim.operator.iso-country"] = ""
    result = call(native_binaries, "draft", {"to": "0612345678", "text": "draft text", "app": "whatsapp"}, fixture)
    assert result["response"]["result"]["isError"] and "SIM card country unknown" in text(result)
    assert not any("'am' 'start'" in command for command in result["commands"])


@pytest.mark.parametrize("mode", ["wrong package", "wrong text", "locked"])
def test_draft_never_confirms_unread_or_wrong_app_text(native_binaries: NativeBinaries, mode: str) -> None:
    fixture = draft_fixture(body="wrong" if mode == "wrong text" else "draft text")
    if mode == "wrong package":
        fixture["state"] = fixture["state"].replace("com.example.sms", "com.wrong.app")
    elif mode == "locked":
        fixture["state"] = fixture["state"].replace("isKeyguardShowing=false", "isKeyguardShowing=true")
    result = call(native_binaries, "draft", {"to": "36179", "text": "draft text"}, fixture)
    body = success(result)
    assert "Nothing sent" in body and "accord=" not in body and "exact text read back" not in body


def notice(package: str, when: int, title: str, body: str, ongoing: bool = False) -> str:
    return (f"\n    NotificationRecord(pkg={package} id=1 flags={'ONGOING_EVENT' if ongoing else '0'} when={when})\n"
            f"      extras={{\n        android.title=String ({title})\n        android.text=String (short)\n"
            f"        android.bigText=String ({body})\n      }}\n")


@pytest.mark.parametrize("all_", [False, True])
def test_notifications_only_active_sorted_multiline_plus_archive(native_binaries: NativeBinaries, all_: bool) -> None:
    dump = "  Notification List:" + notice("com.old", 1000000000000, "old title", "old body")
    dump += notice("com.new", 2000000000000, "new title", "first line\nsecond line")
    dump += notice("com.service", 2000000000001, "service", "running", True)
    dump += "\n  mArchive=Archive\n    StatusBarNotification(pkg=com.dismissed id=1)\n    StatusBarNotification(pkg=com.dismissed id=2)"
    dump += notice("com.stale", 2000000000002, "stale title", "stale body")
    result = call(native_binaries, "notifications", {"all": all_}, {"shell": {"dumpsys notification": dump}})
    body = success(result)
    assert body.index("com.new") < body.index("com.old") and "first line second line" in body
    assert "com.stale" not in body and "Recently dismissed" in body and "com.dismissed ×2" in body
    assert ("com.service" in body) == all_


def test_notification_title_protects_multiline_numeric_and_alphanumeric_codes(native_binaries: NativeBinaries) -> None:
    dump = "  Notification List:" + notice("com.example", 2000000000000, "Verification", "first line\n482913\nA7K9P2")
    body = success(call(native_binaries, "notifications", {}, {"shell": {"dumpsys notification": dump}}))
    assert "482913" not in body and "A7K9P2" not in body and "first line" in body


def test_restore_resolves_packages_hidden_for_user(native_binaries: NativeBinaries) -> None:
    fixture = {"shell": {"pm list packages -u": "package:com.example.hidden\n", "install-existing": "Package com.example.hidden installed for user: 0"}}
    result = call(native_binaries, "apps", {"action": "restore", "app": "com.example.hidden"}, fixture)
    assert "installed" in success(result)
    assert any("'install-existing'" in command for command in result["commands"])


@pytest.mark.parametrize("package", ["com.android.adbd", "com.samsung.android.providers.context", "android"])
def test_exact_core_packages_cannot_be_stopped_or_removed(native_binaries: NativeBinaries, package: str) -> None:
    result = call(native_binaries, "apps", {"action": "stop", "app": package}, {"shell": {"pm list packages": f"package:{package}\n"}})
    assert result["response"]["result"]["isError"] and "Core system" in text(result)
    assert not any("'force-stop'" in command for command in result["commands"])


def test_system_membership_uses_whole_package_not_substring(native_binaries: NativeBinaries) -> None:
    fixture = {"shell": {"pm list packages -s": "package:com.example.app.extra\n", "pm list packages": "package:com.example.app\n", "pm uninstall": "Success"}}
    result = call(native_binaries, "apps", {"action": "uninstall", "app": "com.example.app", "confirm": True}, fixture)
    success(result)
    command = next(command for command in result["commands"] if "'uninstall'" in command)
    assert "'--user'" not in command


def logline(message: str, *, pid: int = 321, tag: str = "AndroidRuntime", time: str = "13:00:00.000") -> str:
    return f"2026-10-29 {time} {pid} 321 E {tag}: {message}\n"


def test_dead_protected_crash_hides_details_keeps_exception_and_own_frames(native_binaries: NativeBinaries) -> None:
    package = "com.revolut.revolut"
    raw = logline("FATAL EXCEPTION: main") + logline(f"Process: {package}, PID: 321")
    raw += logline("java.lang.IllegalStateException: SyntheticAccountBalance")
    raw += logline(f"    at {package}.Main.fail(Main.java:42)")
    raw += logline("    at android.app.Activity.run(Activity.java:90)")
    result = call(native_binaries, "logs", {}, {"shell": {"logcat": raw}})
    body = success(result)
    assert "IllegalStateException" in body and "Main.fail" in body
    assert "SyntheticAccountBalance" not in body and "Activity.run" not in body


def test_recent_protected_dead_pid_never_leaks_following_lines(native_binaries: NativeBinaries) -> None:
    raw = logline("Process: com.revolut.revolut, PID: 321")
    raw += logline("SyntheticAccountBalance") + logline("ordinary app", pid=999)
    body = success(call(native_binaries, "logs", {"kind": "recent"}, {"shell": {"logcat": raw}}))
    assert "ordinary app" in body and "SyntheticAccountBalance" not in body


@pytest.mark.parametrize(("since", "stamp"), [("2h", "2026-10-29 11:30:00.000"), ("13:05", "2026-10-29 13:05:00.000"), ("10-28 14:03", "2026-10-28 14:03:00.000")])
def test_logs_since_uses_phone_clock_and_since_not_line_count(native_binaries: NativeBinaries, since: str, stamp: str) -> None:
    raw = logline("old", time="11:00:00.000") + logline("new", time="13:20:00.000")
    result = call(native_binaries, "logs", {"kind": "recent", "since": since}, {"shell": {"date +%Y": "2026-10-29 13:30:00", "logcat": raw}})
    body = success(result)
    assert any(f"'-t' '{stamp}'" in command for command in result["commands"])
    if since != "10-28 14:03":
        assert "old" not in body and "new" in body


def test_logs_invalid_since_never_reads_log_buffer(native_binaries: NativeBinaries) -> None:
    result = call(native_binaries, "logs", {"since": "yesterday maybe"}, {})
    assert result["response"]["result"]["isError"]
    assert not any("'logcat'" in command for command in result["commands"])


def test_logs_recent_app_filter_uses_start_proc_pid_not_just_package_text(native_binaries: NativeBinaries) -> None:
    raw = logline("Start proc 123:com.example.app/u0a100", pid=1, tag="ActivityManager")
    raw += logline("actual selected log", pid=123) + logline("other app", pid=999)
    result = call(native_binaries, "logs", {"kind": "recent", "app": "com.example.app"}, {"shell": {"pm list packages": "package:com.example.app\n", "logcat": raw}})
    body = success(result)
    assert "actual selected log" in body and "other app" not in body


def test_anrs_merge_dropbox_and_events_without_revealing_protected_reason(native_binaries: NativeBinaries) -> None:
    drop = "2026-10-29 13:00:00 data_app_anr (text, 123 bytes)\nProcess: com.revolut.revolut\nPID: 321\nSubject: SyntheticAccountBalance\n"
    event = logline("[0,321,com.revolut.revolut,1,SyntheticAccountBalance]", tag="am_anr", time="13:00:20.000")
    result = call(native_binaries, "logs", {"kind": "anr"}, {"shell": {"dumpsys dropbox": drop, "logcat": event}})
    body = success(result)
    assert body.count("com.revolut.revolut") == 1 and "SyntheticAccountBalance" not in body
    assert "reason not shown" in body


def test_repeated_crashes_group_without_losing_count(native_binaries: NativeBinaries) -> None:
    raw = ""
    for time in ("13:00:00.000", "13:05:00.000"):
        raw += logline("FATAL EXCEPTION: main", time=time) + logline("Process: com.example.app, PID: 321", time=time)
        raw += logline("java.lang.IllegalStateException: broken", time=time)
    body = success(call(native_binaries, "logs", {}, {"shell": {"logcat": raw}}))
    assert "Repeated 2 time(s)" in body and body.count("IllegalStateException") == 1


def test_crash_filter_accepts_uninstalled_full_package(native_binaries: NativeBinaries) -> None:
    raw = logline("FATAL EXCEPTION: main") + logline("Process: org.gone.app, PID: 321")
    raw += logline("java.lang.IllegalStateException: broken")
    result = call(native_binaries, "logs", {"app": "org.gone.app"}, {"shell": {"logcat": raw}})
    assert "org.gone.app" in success(result)
    assert not any("'pm' 'list'" in command for command in result["commands"])


def test_protected_native_keeps_signal_and_app_backtrace_only(native_binaries: NativeBinaries) -> None:
    raw = logline("*** *** *** ***", tag="DEBUG")
    raw += logline("pid: 123, tid: 123, name: Main >>> com.revolut.revolut <<<", tag="DEBUG")
    raw += logline("signal 6 (SIGABRT), code -1 (SI_QUEUE), fault addr --------", tag="DEBUG")
    raw += logline("Abort message: SyntheticAccountBalance", tag="DEBUG")
    raw += logline("    #00 pc 000000000001aabb /apex/com.android.runtime/lib64/bionic/libc.so", tag="DEBUG")
    raw += logline("    #01 pc 000000000002aabb /data/app/com.revolut.revolut/lib/arm64/libapp.so (CrashHere+4)", tag="DEBUG")
    body = success(call(native_binaries, "logs", {}, {"shell": {"logcat": raw}}))
    assert "signal 6 (SIGABRT)" in body and "libapp.so" in body and "pid 123" in body
    assert "libc.so" not in body and "SyntheticAccountBalance" not in body


def test_deep_application_frame_survives_framework_noise(native_binaries: NativeBinaries) -> None:
    raw = logline("FATAL EXCEPTION: main") + logline("Process: com.example.app, PID: 321")
    raw += logline("java.lang.IllegalStateException: broken")
    raw += "".join(logline(f"    at android.app.Activity.method{index}(Activity.java:90)") for index in range(20))
    raw += logline("    at com.example.app.Main.crashed(Main.kt:42)")
    body = success(call(native_binaries, "logs", {}, {"shell": {"logcat": raw}}))
    assert "Main.crashed(Main.kt:42)" in body and body.count("Activity.method") == 1


@pytest.mark.parametrize("arguments", [
    {"action": "android.intent.action.CALL_PRIVILEGED", "data": "tel:123"},
    {"action": "android.intent.action.RESPOND_VIA_MESSAGE", "data": "smsto:123"},
    {"action": "org.vendor.intent.action.WIPE_DATA"},
    {"action": "android.intent.action.UNINSTALL_PACKAGE", "data": "package:com.example.app"},
    {"data": "https://checkout.stripe.com/pay/test"},
    {"data": "https://www.paypal.com/checkoutnow/session"},
    {"data": "upi://pay?pa=somewhere"},
    {"action": "android.intent.action.VIEW", "extras": {"auto_send": False}},
])
def test_risky_intents_require_approval_before_resolution_or_launch(native_binaries: NativeBinaries, arguments: dict[str, Any]) -> None:
    result = call(native_binaries, "launch", arguments, {})
    assert result["response"]["result"]["isError"]
    assert not any("'am' 'start'" in command or "'resolve-activity'" in command for command in result["commands"])
    assert "Confirmation required" in text(result) or "accord=" in text(result)


def test_plain_sms_compose_is_reversible_and_needs_no_send_agreement(native_binaries: NativeBinaries) -> None:
    fixture = draft_fixture()
    fixture["shell"].update({"resolve-activity": "com.example.sms/.Main", "query-activities": "com.example.sms/.Main"})
    result = call(native_binaries, "launch", {"action": "android.intent.action.SENDTO", "data": "smsto:123", "then": "none"}, fixture)
    assert "foreground read back" in success(result)
    assert any("'am' 'start'" in command for command in result["commands"])


def test_protected_candidate_blocks_intent_before_launch(native_binaries: NativeBinaries) -> None:
    fixture = {"shell": {"resolve-activity": "com.example.app/.Main", "query-activities": "com.example.app/.Main\ncom.revolut.revolut/.Main"}}
    result = call(native_binaries, "launch", {"data": "https://ordinary.example/path"}, fixture)
    assert result["response"]["result"]["isError"] and "protected" in text(result)
    assert not any("'am' 'start'" in command for command in result["commands"])
