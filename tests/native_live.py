"""Interactive native-only QA driver. Commands are explicit JSON tool calls on stdin.

It never sends a gesture by itself. Images are decoded to the product's local QA cache;
base64 is never printed. Stop with {"quit": true}. The production HTTP daemon is untouched.
Media commands require expect_foreground and an immediately preceding unlocked,
screen-on phone status matching it. This QA condition never changes the production tool.
"""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def verify_media_precondition(command: dict, status: dict) -> None:
    expected = command.get("expect_foreground")
    if not isinstance(expected, str) or not expected.strip():
        raise ValueError("Media QA requires an explicit expect_foreground package")
    if (status.get("foreground") != expected or status.get("screen_on") is not True
            or status.get("locked") is not False or status.get("code_required") is not False
            or not status.get("model")):
        raise ValueError("Media QA skipped: fresh phone status does not match the expected unlocked, lit screen")


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    local = Path(os.environ["LOCALAPPDATA"]) / "NexusAndroid"
    executable = Path(os.environ.get("NEXUS_ANDROID_NATIVE_EXE", str(local / "qualified-candidate" / "NexusAndroid.exe")))
    process = subprocess.Popen([str(executable), "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8", creationflags=0x08000000)
    assert process.stdin is not None and process.stdout is not None
    image_number = 0
    try:
        print(json.dumps({"ready": True, "pid": process.pid, "executable": str(executable)}), flush=True)
        for call_number, line in enumerate(sys.stdin, 1):
            command = json.loads(line)
            if command.get("quit"):
                break
            name, arguments = command["tool"], command.get("args", {})
            if name == "capture" and arguments.get("action", "screenshot") in {"screenshot", "start", "record"}:
                preflight = {"jsonrpc": "2.0", "id": f"{call_number}-media-preflight", "method": "tools/call",
                             "params": {"name": "phone", "arguments": {}}}
                process.stdin.write(json.dumps(preflight) + "\n")
                process.stdin.flush()
                observed = json.loads(process.stdout.readline())
                try:
                    verify_media_precondition(command, observed.get("result", {}).get("structuredContent", {}))
                except ValueError as error:
                    print(json.dumps({"tool": name, "skipped": True, "reason": str(error)}), flush=True)
                    continue
            request = {"jsonrpc": "2.0", "id": call_number, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}}
            began = time.monotonic()
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
            response = json.loads(process.stdout.readline())
            for content in response.get("result", {}).get("content", []):
                if content.get("type") == "image":
                    image_number += 1
                    folder = local / "live-qa"
                    folder.mkdir(parents=True, exist_ok=True)
                    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[content["mimeType"]]
                    target = folder / f"native-{process.pid}-{image_number}{suffix}"
                    payload = base64.b64decode(content.pop("data"))
                    target.write_bytes(payload)
                    content.update(path=str(target), bytes=len(payload))
            print(json.dumps({"tool": name, "seconds": round(time.monotonic() - began, 3), "response": response},
                             ensure_ascii=False), flush=True)
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
