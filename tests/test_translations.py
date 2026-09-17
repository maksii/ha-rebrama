"""Consistency checks between the code and its translation files."""

from __future__ import annotations

import json
from pathlib import Path
import re

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "rebrama"


def _strings() -> dict:
    return json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))


def test_english_translation_matches_strings() -> None:
    """translations/en.json is what runs; strings.json must not drift from it."""
    english = json.loads(
        (COMPONENT / "translations" / "en.json").read_text(encoding="utf-8")
    )
    assert english == _strings()


def test_every_exception_key_is_translated() -> None:
    """Every translation_key raised in code has a message in strings.json."""
    exceptions = _strings()["exceptions"]
    used = {
        key
        for path in COMPONENT.glob("*.py")
        for key in re.findall(
            r'translation_key="(\w+)"', path.read_text(encoding="utf-8")
        )
    }
    assert used
    assert used <= set(exceptions)
    assert set(exceptions) <= used, "unused exception translations"


def test_every_entity_key_is_translated() -> None:
    """Every entity translation_key has a name (and platform) in strings.json."""
    entity_strings = _strings()["entity"]
    for platform in ("binary_sensor", "button", "calendar", "sensor"):
        source = (COMPONENT / f"{platform}.py").read_text(encoding="utf-8")
        keys = set(re.findall(r'_attr_translation_key = "(\w+)"', source))
        assert keys, platform
        assert keys == set(entity_strings[platform]), platform
