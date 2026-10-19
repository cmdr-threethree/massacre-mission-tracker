from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from massacre_tracker.domain.models import StationContext
from massacre_tracker.journal.events import EventEnvelope
from tests.builders import BASE_TIME


def envelope(
    event: str,
    data: dict[str, Any] | None = None,
    *,
    timestamp: datetime = BASE_TIME,
    path: Path = Path("Journal.2026-10-19T120000.01.log"),
    line_number: int = 0,
    live: bool | None = True,
    station: StationContext | None = None,
    line_hash: str | None = None,
) -> EventEnvelope:
    payload = {"event": event, "timestamp": timestamp.isoformat()}
    if data:
        payload.update(data)
    return EventEnvelope(
        event=event,
        timestamp=timestamp,
        data=payload,
        path=path,
        filename=path.name,
        line_number=line_number,
        live=live,
        station=station or StationContext(),
        line_hash=line_hash if line_hash is not None else f"{path}:{line_number}:{event}",
    )


def mission_payload(
    mission_id: int,
    *,
    faction: str = "Issuer A",
    target_faction: str = "Target Faction",
    kills: int = 10,
    reward: int = 1000,
    target_type: str = "MissionUtil_FactionTag_Pirate",
    wing: bool = False,
) -> dict[str, Any]:
    return {
        "MissionID": mission_id,
        "Faction": faction,
        "DestinationSystem": "Destination",
        "TargetFaction": target_faction,
        "LocalisedName": "Kill pirates",
        "Reward": reward,
        "KillCount": kills,
        "Wing": wing,
        "TargetType": target_type,
    }
