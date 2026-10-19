"""Mission acceptance eligibility."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..journal.events import parse_timestamp
from .models import Mission, MissionState, StationContext
from .values import as_bool, as_int, as_text

PIRATE_TARGET_TAG = "missionutil_factiontag_pirate"


def is_eligible_mission(
    data: dict[str, Any],
    station: StationContext,
    live: bool | None,
) -> bool:
    """Return whether a ``MissionAccepted`` event should be tracked."""

    if live is False:
        return False
    if not station.station:
        return False

    if "KillCount" not in data:
        return False
    if as_int(data.get("KillCount"), 0) <= 0:
        return False

    target_faction = as_text(data.get("TargetFaction"))
    if not target_faction or not target_faction.strip():
        return False

    target_type = as_text(data.get("TargetType"))
    if not target_type or PIRATE_TARGET_TAG not in target_type.casefold():
        return False

    return True


def build_mission(
    data: dict[str, Any],
    timestamp: datetime,
    station: StationContext,
    live: bool | None,
) -> Mission | None:
    if not is_eligible_mission(data, station, live):
        return None

    mission_id = _mission_id(data)
    if mission_id is None:
        return None

    target_faction = as_text(data.get("TargetFaction"))
    contract = as_int(data.get("KillCount"), 0)
    if not target_faction or contract <= 0:
        return None

    return Mission(
        mission_id=mission_id,
        source_system=station.system,
        system_address=station.system_address,
        source_station=station.station,
        issuing_faction=as_text(data.get("Faction")) or "",
        destination_system=as_text(data.get("DestinationSystem")),
        target_faction=target_faction,
        localised_name=as_text(data.get("LocalisedName")),
        reward=as_int(data.get("Reward"), 0),
        contract_kills=contract,
        progress_kills=0,
        wing=as_bool(data.get("Wing")),
        accepted_at=timestamp,
        expires_at=parse_timestamp(data.get("Expiry")),
        state=MissionState.ACTIVE,
    )


def _mission_id(data: dict[str, Any]) -> int | None:
    value = data.get("MissionID")
    if value is None:
        return None
    mission_id = as_int(value, -1)
    if mission_id < 0:
        return None
    return mission_id
