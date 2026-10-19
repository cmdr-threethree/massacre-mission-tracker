"""Journal event constants and event envelopes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..domain.models import StationContext

RELEVANT_EVENTS = frozenset(
    {
        "Fileheader",
        "Commander",
        "Location",
        "Docked",
        "Undocked",
        "MissionAccepted",
        "MissionRedirected",
        "MissionCompleted",
        "MissionAbandoned",
        "MissionFailed",
        "Missions",
        "Bounty",
    }
)

GAMEPLAY_EVENTS = frozenset(
    {
        "MissionAccepted",
        "MissionRedirected",
        "MissionCompleted",
        "MissionAbandoned",
        "MissionFailed",
        "Missions",
        "Bounty",
    }
)

STATION_EVENTS = frozenset({"Location", "Docked", "Undocked"})

_VERSION_RE = re.compile(r"\s*(\d+)")


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 journal timestamp as an aware UTC datetime."""

    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def game_major_version(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    match = _VERSION_RE.match(value)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:  # pragma: no cover - regex guarantees digits
        return None


def is_live_version(value: Any) -> bool:
    major = game_major_version(value)
    return major is not None and major >= 4


@dataclass(slots=True)
class EventEnvelope:
    """A parsed event plus the session context at ingestion time."""

    event: str
    timestamp: datetime
    data: dict[str, Any]
    path: Path
    filename: str
    line_number: int
    live: bool | None
    station: StationContext
    line_hash: str = ""
