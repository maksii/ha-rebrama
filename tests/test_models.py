"""Tests for the wire-format parsing in the Rebrama models."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.rebrama.models import (
    OpenLog,
    Profile,
    TempAccess,
    _epoch_to_datetime,
)


def test_naive_iso_timestamps_become_utc() -> None:
    """Instants without an offset are treated as UTC (TIMESTAMP sensors need it)."""
    log = OpenLog.from_api({"createdAt": "2026-06-06T10:00:00"})
    assert log.created_at == datetime(2026, 6, 6, 10, tzinfo=UTC)

    profile = Profile.from_api({"id": "u1", "validUntil": "2027-01-01T00:00:00+02:00"})
    assert profile.valid_until is not None
    assert profile.valid_until.utcoffset().total_seconds() == 7200

    assert Profile.from_api({"id": "u1", "validUntil": "garbage"}).valid_until is None
    assert OpenLog.from_api({"createdAt": None}).created_at is None


def test_epoch_seconds_and_milliseconds() -> None:
    """Epoch values are accepted in seconds or milliseconds; junk is ignored."""
    assert _epoch_to_datetime(1_780_000_000) == datetime.fromtimestamp(
        1_780_000_000, UTC
    )
    assert _epoch_to_datetime(1_780_000_000_000) == datetime.fromtimestamp(
        1_780_000_000, UTC
    )
    assert _epoch_to_datetime(0) is None
    assert _epoch_to_datetime(True) is None
    assert _epoch_to_datetime("123") is None
    assert _epoch_to_datetime(10**30) is None


def test_temp_access_is_active() -> None:
    """A link without an end never expires; otherwise dateEnd decides."""
    now = datetime(2026, 6, 6, 12, tzinfo=UTC)
    open_ended = TempAccess.from_api({"url": "https://rebrama.com/access/a"})
    assert open_ended.is_active(now)
    expired = TempAccess.from_api(
        {"url": "https://rebrama.com/access/b", "dateEnd": int(now.timestamp()) - 1}
    )
    assert not expired.is_active(now)
