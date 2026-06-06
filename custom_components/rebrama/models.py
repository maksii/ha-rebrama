"""Typed data models for the Rebrama API.

Field names map to the wire JSON exactly as produced by the official app
(including the production typo ``aceessPointName``). All models are frozen so
the coordinator can use ``always_update=False`` and skip redundant state writes
when nothing changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .const import BASE_URL, TEMP_ACCESS_PATH

# Epoch values above this are milliseconds, not seconds (seconds won't reach
# 1e11 until the year 5138). The temp-access endpoints document their date unit
# as unverified (s vs ms), so normalize defensively.
_EPOCH_MS_THRESHOLD = 1e11


def _epoch_to_datetime(value: Any) -> datetime | None:
    """Convert an epoch value (seconds *or* milliseconds) to an aware datetime."""
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    seconds = value / 1000 if value > _EPOCH_MS_THRESHOLD else value
    try:
        return dt_util.utc_from_timestamp(seconds)
    except (OverflowError, OSError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class AuthTokens:
    """A pair of rotating JWT tokens returned by the auth endpoints."""

    access: str
    refresh: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> AuthTokens:
        """Build from a ``{"access": ..., "refresh": ...}`` payload."""
        return cls(access=str(data["access"]), refresh=str(data["refresh"]))


@dataclass(frozen=True, slots=True)
class Profile:
    """The authenticated user's profile (``GET /api/users/me``)."""

    user_id: str
    phone: str
    valid_until: datetime | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Profile:
        """Build from a profile payload.

        ``validUntil`` (ISO8601 subscription expiry) is shipped by the API but
        ignored by the official app's DTOs; we surface it as a sensor.
        """
        valid = data.get("validUntil")
        return cls(
            user_id=str(data["id"]),
            phone=str(data.get("phone") or ""),
            valid_until=dt_util.parse_datetime(valid) if valid else None,
        )


@dataclass(frozen=True, slots=True)
class AccessPoint:
    """A single door/gate that can be opened."""

    id: str
    name: str
    is_online: bool
    can_share_access: bool
    place_id: str
    place_name: str


@dataclass(frozen=True, slots=True)
class Place:
    """A place that owns one or more access points."""

    id: str
    name: str
    can_manage: bool
    is_owner: bool
    access_points: dict[str, AccessPoint]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Place:
        """Build a place (and its nested access points) from the API payload."""
        place_id = str(data["id"])
        place_name = str(data.get("name") or "")
        access_points: dict[str, AccessPoint] = {}
        for raw in data.get("accessPoints") or []:
            ap_id = str(raw["id"])
            access_points[ap_id] = AccessPoint(
                id=ap_id,
                name=str(raw.get("name") or ""),
                is_online=bool(raw.get("isOnline")),
                can_share_access=bool(raw.get("canShareAccess")),
                place_id=place_id,
                place_name=place_name,
            )
        return cls(
            id=place_id,
            name=place_name,
            can_manage=bool(data.get("canManage")),
            is_owner=bool(data.get("isOwner")),
            access_points=access_points,
        )


@dataclass(frozen=True, slots=True)
class OpenLog:
    """A single entry from a place's opening history."""

    created_at: datetime | None
    user_phone: str | None
    user_info: str | None
    access_point_name: str | None
    is_temp_access: bool

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> OpenLog:
        """Build from one ``open-logs`` item."""
        created = data.get("createdAt")
        return cls(
            created_at=dt_util.parse_datetime(created) if created else None,
            user_phone=data.get("userPhone"),
            user_info=data.get("userInfo"),
            # The production API ships the field misspelled as ``aceessPointName``;
            # accept the corrected spelling too in case it is ever fixed.
            access_point_name=(
                data.get("aceessPointName") or data.get("accessPointName")
            ),
            is_temp_access=bool(data.get("isTempAccess")),
        )


@dataclass(frozen=True, slots=True)
class TempAccess:
    """A time-bounded share link (``GET /api/temp-accesses/user``).

    The list payload exposes ``link`` and ``url``; the slug needed for
    ``{slug}/info`` and ``DELETE {slug}`` is the trailing path segment.
    """

    slug: str
    url: str
    description: str
    date_start: datetime | None
    date_end: datetime | None
    uses_number: int | None

    def is_active(self, now: datetime) -> bool:
        """Return whether the link has not yet expired at ``now``."""
        return self.date_end is None or self.date_end > now

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> TempAccess:
        """Build from one ``temp-accesses`` item."""
        raw = str(data.get("url") or data.get("link") or "")
        if raw and "://" not in raw:
            # The API gave us a bare slug; rebuild the full share URL.
            slug = raw.strip("/")
            url = f"{BASE_URL}/{TEMP_ACCESS_PATH}/{slug}"
        else:
            slug = raw.rstrip("/").rsplit("/", 1)[-1] if raw else ""
            url = raw
        uses = data.get("usesNumber")
        return cls(
            slug=slug,
            url=url,
            description=str(data.get("description") or ""),
            date_start=_epoch_to_datetime(data.get("dateStart")),
            date_end=_epoch_to_datetime(data.get("dateEnd")),
            uses_number=int(uses) if isinstance(uses, (int, float)) else None,
        )
