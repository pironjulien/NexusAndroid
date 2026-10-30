"""The interactive QA driver must not record a different phone scene by accident."""

import pytest
from native_live import verify_media_precondition


def test_media_requires_an_explicit_expected_application() -> None:
    with pytest.raises(ValueError, match="explicit expect_foreground"):
        verify_media_precondition({}, {})


@pytest.mark.parametrize("changed", [
    {"foreground": "com.other.application"}, {"screen_on": False}, {"locked": True},
    {"code_required": True}, {"model": ""}, {"screen_on": None},
])
def test_media_refuses_a_mismatched_or_incoherent_fresh_screen(changed: dict) -> None:
    status = {"foreground": "com.example.launcher", "screen_on": True, "locked": False,
              "code_required": False, "model": "Fixture Fold", **changed}
    with pytest.raises(ValueError, match="skipped"):
        verify_media_precondition({"expect_foreground": "com.example.launcher"}, status)


def test_media_accepts_only_the_expected_unlocked_and_lit_screen() -> None:
    verify_media_precondition({"expect_foreground": "com.example.launcher"}, {
        "foreground": "com.example.launcher", "screen_on": True, "locked": False,
        "code_required": False, "model": "Fixture Fold",
    })
