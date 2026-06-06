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

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Profile:
        """Build from a profile payload."""
        return cls(user_id=str(data["id"]), phone=str(data.get("phone") or ""))


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
