from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE_TIME = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def event(name: str, timestamp: datetime | str | None = None, **fields: Any) -> str:
    if timestamp is None:
        timestamp = BASE_TIME
    if isinstance(timestamp, datetime):
        timestamp = iso(timestamp)
    payload: dict[str, Any] = {"timestamp": timestamp, "event": name}
    payload.update(fields)
    return json.dumps(payload, separators=(",", ":"))


def mission_accepted(
    mission_id: int,
    *,
    timestamp: datetime | str | None = None,
    faction: str = "Issuer A",
    target_faction: str = "Target Faction",
    destination: str = "Destination",
    kills: int = 10,
    reward: int = 1000,
    wing: bool = False,
    target_type: str = "MissionUtil_FactionTag_Pirate",
    localised_name: str = "Kill pirates",
    expiry: datetime | str | None = None,
) -> str:
    fields: dict[str, Any] = {
        "MissionID": mission_id,
        "Faction": faction,
        "DestinationSystem": destination,
        "TargetFaction": target_faction,
        "LocalisedName": localised_name,
        "Reward": reward,
        "KillCount": kills,
        "Wing": wing,
        "TargetType": target_type,
    }
    if expiry is not None:
        fields["Expiry"] = iso(expiry) if isinstance(expiry, datetime) else expiry
    return event("MissionAccepted", timestamp, **fields)


def bounty(
    *,
    timestamp: datetime | str | None = None,
    target: str = "sidewinder",
    victim_faction: str = "Target Faction",
    reward: int = 10000,
) -> str:
    return event(
        "Bounty",
        timestamp,
        Target=target,
        VictimFaction=victim_faction,
        TotalReward=reward,
    )


def write_journal(
    path: Path,
    lines: list[str],
    *,
    append: bool = False,
    newline: bool = True,
) -> Path:
    text = "\n".join(lines)
    if lines and newline:
        text += "\n"
    with path.open("a" if append else "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


def journal_path(
    directory: Path,
    timestamp: datetime = BASE_TIME,
    sequence: int = 1,
) -> Path:
    stamp = timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    return directory / f"Journal.{stamp}.{sequence:02d}.log"


def fileheader(
    timestamp: datetime | str | None = None,
    gameversion: str = "4.0.0.100",
) -> str:
    return event("Fileheader", timestamp, gameversion=gameversion)


def commander(
    fid: str = "F100",
    name: str = "Jameson",
    timestamp: datetime | str | None = None,
) -> str:
    return event("Commander", timestamp, Name=name, FID=fid)


def docked(
    system: str = "Sol",
    station: str = "Galileo",
    timestamp: datetime | str | None = None,
    system_address: int = 10477373803,
) -> str:
    return event(
        "Docked",
        timestamp,
        StarSystem=system,
        SystemAddress=system_address,
        StationName=station,
    )


def location_docked(
    system: str = "Sol",
    station: str = "Galileo",
    timestamp: datetime | str | None = None,
    system_address: int = 10477373803,
) -> str:
    return event(
        "Location",
        timestamp,
        Docked=True,
        StarSystem=system,
        SystemAddress=system_address,
        StationName=station,
    )


def mission_event(
    name: str,
    mission_id: int,
    *,
    timestamp: datetime | str | None = None,
    **fields: Any,
) -> str:
    return event(name, timestamp, MissionID=mission_id, **fields)


def at(seconds: int) -> datetime:
    return BASE_TIME + timedelta(seconds=seconds)
