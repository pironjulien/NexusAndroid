"""Cibles de fuzzing (fuzz/targets.py) sans Atheris : octets pseudo-aléatoires à graine fixe, sur Windows aussi."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fuzz"))

import targets  # noqa: E402  # pyright: ignore[reportMissingImports]


def test_targets_hold_on_random_inputs() -> None:
    rng = random.Random(1618)
    for _ in range(20000):
        targets.target(rng.randbytes(rng.randrange(0, 200)))


def test_stack_frame_lines_are_masked_too() -> None:
    """Contre-exemples de la relecture de Codex : une ligne qui commence comme une pile ne garde plus un jeton."""
    for seed in (b"\x03\x07at a.b(\x00", b"\x03\x07#00 pc \x00"):
        targets.mask(seed)
    targets.mask(bytes.fromhex("0207617420612e6228000307010800020201"))  # « at a.b( | OTP : 471802 | »


def test_crashes_found_by_the_fuzzer() -> None:
    import base64

    targets.target(base64.b64decode("cnJycnJycnK0cnJyMSB8IGFhYUBhYS5mcjBhNDhiMTYzYf8="))  # copie collée autour (CI)
