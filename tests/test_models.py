"""Tests for the wire-format parsing in the Rebrama models."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.rebrama.models import (
    OpenLog,
    Profile,
    TempAccess,
    _to_datetime,
)

EPOCH = 1_780_000_000
AS_DATETIME = datetime.fromtimestamp(EPOCH, UTC)


def test_timestamps_accept_every_plausible_wire_shape() -> None:
    """Epoch seconds/milliseconds, as numbers or strings, and ISO8601 all parse."""
    assert _to_datetime(EPOCH) == AS_DATETIME
    assert _to_datetime(EPOCH * 1000) == AS_DATETIME
    assert _to_datetime(str(EPOCH)) == AS_DATETIME
    assert _to_datetime(f"{EPOCH}000") == AS_DATETIME
    assert _to_datetime(AS_DATETIME.isoformat()) == AS_DATETIME
    # Naive instants are treated as UTC (TIMESTAMP sensors need an offset).
    assert _to_datetime("2026-06-06T10:00:00") == datetime(2026, 6, 6, 10, tzinfo=UTC)
    with_offset = _to_datetime("2027-01-01T00:00:00+02:00")
    assert with_offset is not None
    assert with_offset.utcoffset().total_seconds() == 7200
    for junk in (None, "", " ", "garbage", 0, -1, True, 10**30, "nan", [EPOCH]):
        assert _to_datetime(junk) is None, junk


def test_models_parse_dates_through_the_shared_parser() -> None:
    """Every dated field goes through the tolerant parser."""
    profile = Profile.from_api({"id": "u1", "validUntil": str(EPOCH)})
    assert profile.valid_until == AS_DATETIME
    assert Profile.from_api({"id": "u1"}).valid_until is None
    log = OpenLog.from_api({"createdAt": AS_DATETIME.isoformat()})
    assert log.created_at == AS_DATETIME
    access = TempAccess.from_api(
        {
            "url": "https://rebrama.com/access/a",
            "dateStart": str(EPOCH),
            "dateEnd": EPOCH * 1000,
        }
    )
    assert (access.date_start, access.date_end) == (AS_DATETIME, AS_DATETIME)


def test_temp_access_is_active() -> None:
    """A link without an end never expires; otherwise dateEnd decides."""
    now = datetime(2026, 6, 6, 12, tzinfo=UTC)
    open_ended = TempAccess.from_api({"url": "https://rebrama.com/access/a"})
    assert open_ended.is_active(now)
    expired = TempAccess.from_api(
        {"url": "https://rebrama.com/access/b", "dateEnd": int(now.timestamp()) - 1}
    )
    assert not expired.is_active(now)
