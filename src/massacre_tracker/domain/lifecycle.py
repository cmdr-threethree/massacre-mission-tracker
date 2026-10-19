"""Mission lifecycle transitions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .eligibility import build_mission
from .models import CommanderTracker, Mission, MissionState, StationContext
from .values import as_int

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
            return None

    mission = build_mission(data, timestamp, station, live)
    if mission is None:
        return None
    if not tracker.add_mission(mission):
        return None
    return mission


def apply_mission_redirected(tracker: CommanderTracker, data: dict[str, Any]) -> bool:
    mission = _find(tracker, data)
    if mission is None:
        return False
    mission.progress_kills = mission.contract_kills
    mission.state = MissionState.REDIRECTED
    return True


def apply_mission_completed(tracker: CommanderTracker, data: dict[str, Any]) -> bool:
    mission = _find(tracker, data)
    if mission is None:
        return False
    mission.state = MissionState.COMPLETED
    mission.reward = as_int(data.get("Reward"), 0)
    return True


def apply_mission_abandoned(tracker: CommanderTracker, data: dict[str, Any]) -> bool:
    mission = _find(tracker, data)
    if mission is None:
        return False
    mission.state = MissionState.ABANDONED
    mission.reward = 0
    return True


def apply_mission_failed(tracker: CommanderTracker, data: dict[str, Any]) -> bool:
    mission = _find(tracker, data)
    if mission is None:
        return False
    mission.state = MissionState.FAILED
    mission.reward = 0
    return True


def reconcile_missions(tracker: CommanderTracker, data: dict[str, Any]) -> int:
    """Apply a ``Missions`` snapshot without creating any missions."""

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

    changed = 0
    for mission_id, mission in tracker.missions.items():
        if mission_id not in listed and mission.state != MissionState.COMPLETED:
            mission.state = MissionState.COMPLETED
            changed += 1
    return changed


def set_manual_state(
    tracker: CommanderTracker,
    mission_id: int,
    state: MissionState,
) -> bool:
    mission = tracker.get_mission(mission_id)
    if mission is None:
        return False
    mission.state = state
    if state in _TERMINAL_REWARD_ZERO:
        mission.reward = 0
    return True


def _find(tracker: CommanderTracker, data: dict[str, Any]) -> Mission | None:
    mission_id = as_int(data.get("MissionID"), -1)
    if mission_id < 0:
        return None
    return tracker.get_mission(mission_id)
