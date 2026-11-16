"""Mission lifecycle transitions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..journal.events import parse_timestamp
from .eligibility import build_mission, is_smuggler_target
from .models import (
    CommanderTracker,
    IgnoredMission,
    Mission,
    MissionKind,
    MissionState,
    StationContext,
)
from .values import as_int, as_text

_TERMINAL_REWARD_ZERO = frozenset({MissionState.ABANDONED, MissionState.FAILED})


def apply_mission_accepted(
    tracker: CommanderTracker,
    data: dict[str, Any],
    timestamp: datetime,
    station: StationContext,
    live: bool | None,
) -> Mission | None:
    """Create a mission. Duplicate IDs are idempotent."""

    mission_id = as_int(data.get("MissionID"), -1)
    if mission_id >= 0:
        existing = tracker.get_mission(mission_id)
        if existing is not None:
            tracker.last_mission_kind = existing.kind
            return None

    mission = build_mission(data, timestamp, station, live)
    if mission is None:
        return None
    if not tracker.add_mission(mission):
        return None
    tracker.last_mission_kind = mission.kind
    return mission


def record_ignored_mission(
    tracker: CommanderTracker,
    data: dict[str, Any],
    timestamp: datetime,
    station: StationContext,
) -> IgnoredMission | None:
    """Remember a smuggler-target mission that is not tracked."""

    if not is_smuggler_target(data):
        return None
    if as_int(data.get("KillCount"), 0) <= 0:
        return None
    mission_id = as_int(data.get("MissionID"), -1)
    if mission_id < 0:
        return None

    ignored = IgnoredMission(
        mission_id=mission_id,
        source_system=station.system,
        source_station=station.station,
        target_faction=as_text(data.get("TargetFaction")) or "",
        target_type_label=as_text(data.get("TargetType_Localised")),
        localised_name=as_text(data.get("LocalisedName")),
        contract_kills=as_int(data.get("KillCount"), 0),
        reward=as_int(data.get("Reward"), 0),
        accepted_at=timestamp,
        expires_at=parse_timestamp(data.get("Expiry")),
    )
    if not tracker.add_ignored_mission(ignored):
        return None
    return ignored


def apply_mission_redirected(tracker: CommanderTracker, data: dict[str, Any]) -> bool:
    mission = _find(tracker, data)
    if mission is None:
        return False
    tracker.last_mission_kind = mission.kind
    if mission.kind == MissionKind.MINING:
        new_system = as_text(data.get("NewDestinationSystem"))
        new_station = as_text(data.get("NewDestinationStation"))
        if new_system:
            mission.destination_system = new_system
        if new_station:
            mission.destination_station = new_station
        return True
    mission.progress_kills = mission.contract_kills
    mission.state = MissionState.REDIRECTED
    return True


def apply_cargo_depot(tracker: CommanderTracker, data: dict[str, Any]) -> bool:
    """Apply a wing mining mission delivery update."""

    mission = _find(tracker, data)
    if mission is None or mission.kind != MissionKind.MINING:
        return False

    update_type = (as_text(data.get("UpdateType")) or "").casefold()
    if update_type and update_type != "deliver":
        return False

    delivered = as_int(data.get("ItemsDelivered"), -1)
    if delivered < 0:
        delivered = as_int(data.get("Count"), 0)
    mission.set_progress(max(mission.progress_kills, delivered))
    mission.normalise_state()
    tracker.last_mission_kind = MissionKind.MINING
    return True


def apply_mission_completed(tracker: CommanderTracker, data: dict[str, Any]) -> bool:
    tracker.remove_ignored_mission(as_int(data.get("MissionID"), -1))
    mission = _find(tracker, data)
    if mission is None:
        return False
    tracker.last_mission_kind = mission.kind
    mission.state = MissionState.COMPLETED
    mission.reward = as_int(data.get("Reward"), 0)
    return True


def apply_mission_abandoned(tracker: CommanderTracker, data: dict[str, Any]) -> bool:
    tracker.remove_ignored_mission(as_int(data.get("MissionID"), -1))
    mission = _find(tracker, data)
    if mission is None:
        return False
    tracker.last_mission_kind = mission.kind
    mission.state = MissionState.ABANDONED
    mission.reward = 0
    return True


def apply_mission_failed(tracker: CommanderTracker, data: dict[str, Any]) -> bool:
    tracker.remove_ignored_mission(as_int(data.get("MissionID"), -1))
    mission = _find(tracker, data)
    if mission is None:
        return False
    tracker.last_mission_kind = mission.kind
    mission.state = MissionState.FAILED
    mission.reward = 0
    return True


def reconcile_missions(tracker: CommanderTracker, data: dict[str, Any]) -> int:
    """Apply a ``Missions`` snapshot without creating any missions."""

    listed = _listed_mission_ids(data)

    changed = 0
    for mission_id, mission in tracker.missions.items():
        if mission_id not in listed and mission.state != MissionState.COMPLETED:
            mission.state = MissionState.COMPLETED
            changed += 1
    return changed


def reconcile_ignored_missions(
    tracker: CommanderTracker,
    data: dict[str, Any],
) -> int:
    """Forget ignored missions that are no longer active in a snapshot."""

    listed = _listed_mission_ids(data)
    stale = [
        mission_id
        for mission_id in tracker.ignored_missions
        if mission_id not in listed
    ]
    for mission_id in stale:
        tracker.remove_ignored_mission(mission_id)
    return len(stale)


def _listed_mission_ids(data: dict[str, Any]) -> set[int]:
    listed: set[int] = set()
    for key in ("Active", "Complete", "Failed"):
        entries = data.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            mission_id = as_int(entry.get("MissionID"), -1)
            if mission_id >= 0:
                listed.add(mission_id)
    return listed


def set_manual_state(
    tracker: CommanderTracker,
    mission_id: int,
    state: MissionState,
) -> bool:
    mission = tracker.get_mission(mission_id)
    if mission is None:
        return False
    mission.state = state
    tracker.last_mission_kind = mission.kind
    if state in _TERMINAL_REWARD_ZERO:
        mission.reward = 0
    return True


def _find(tracker: CommanderTracker, data: dict[str, Any]) -> Mission | None:
    mission_id = as_int(data.get("MissionID"), -1)
    if mission_id < 0:
        return None
    return tracker.get_mission(mission_id)
