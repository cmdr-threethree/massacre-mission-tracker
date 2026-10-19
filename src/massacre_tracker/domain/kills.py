"""Bounty eligibility, automatic kill credit, and manual adjustments."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from .models import Bounty, CommanderTracker, Mission, MissionState
from .values import as_int, as_text

StackKey = tuple[str, str]


def bounty_is_eligible(data: dict[str, Any]) -> bool:
    """Apply the exact, case-sensitive bounty filters from the spec."""

    victim = as_text(data.get("VictimFaction")) or ""
    if "faction_none" in victim:
        return False
    if "faction_Pirate" in victim:
        return False

    target = as_text(data.get("Target")) or ""
    if "suit" in target:
        return False

    if as_int(data.get("TotalReward"), 0) <= 0:
        return False

    return True


def apply_bounty(
    tracker: CommanderTracker,
    data: dict[str, Any],
    timestamp: datetime,
) -> bool:
    """Record an eligible bounty and credit at most one mission per stack."""

    if not bounty_is_eligible(data):
        return False

    victim = as_text(data.get("VictimFaction")) or ""
    target = as_text(data.get("Target")) or "Unknown"
    reward = as_int(data.get("TotalReward"), 0)
    tracker.record_bounty(
        Bounty(
            timestamp=timestamp,
            ship_type=target,
            victim_faction=victim,
            reward=reward,
        )
    )

    victim_cf = victim.casefold()
    if not victim_cf:
        return True

    stacks: dict[StackKey, list[Mission]] = defaultdict(list)
    for mission in tracker.missions.values():
        if mission.is_stack_member and mission.progress_kills < mission.contract_kills:
            key = (
                mission.issuing_faction.casefold(),
                mission.target_faction.casefold(),
            )
            stacks[key].append(mission)

    for key in sorted(stacks):
        if key[1] != victim_cf:
            continue
        mission = min(stacks[key], key=lambda m: m.sort_key)
        _increment(mission)
    return True


def adjust_mission(
    tracker: CommanderTracker,
    mission_id: int,
    delta: int,
) -> bool:
    """Adjust one selected mission, clamping and updating state."""

    mission = tracker.get_mission(mission_id)
    if mission is None or mission.is_terminal:
        return False
    mission.set_progress(mission.progress_kills + delta)
    _normalise_state(mission)
    return True


def adjust_all_stacks(tracker: CommanderTracker, delta: int) -> int:
    """Adjust one mission per issuing/target stack."""

    if delta == 0:
        return 0

    groups = _non_terminal_groups(tracker)
    changed = 0
    for key in sorted(groups):
        missions = groups[key]
        ordered = sorted(
            missions,
            key=lambda m: m.sort_key,
            reverse=delta < 0,
        )
        for mission in ordered:
            if delta > 0:
                if mission.progress_kills >= mission.contract_kills:
                    continue
                _increment(mission)
                changed += 1
                break
            if mission.progress_kills > 0:
                mission.set_progress(mission.progress_kills - 1)
                _normalise_state(mission)
                changed += 1
                break
    return changed


def _increment(mission: Mission) -> None:
    mission.set_progress(mission.progress_kills + 1)
    _normalise_state(mission)


def _normalise_state(mission: Mission) -> None:
    if mission.progress_kills >= mission.contract_kills:
        mission.state = MissionState.REDIRECTED
    elif mission.state == MissionState.REDIRECTED:
        mission.state = MissionState.ACTIVE


def _non_terminal_groups(
    tracker: CommanderTracker,
) -> dict[StackKey, list[Mission]]:
    groups: dict[StackKey, list[Mission]] = defaultdict(list)
    for mission in tracker.missions.values():
        if mission.is_terminal:
            continue
        key = (
            mission.issuing_faction.casefold(),
            mission.target_faction.casefold(),
        )
        groups[key].append(mission)
    return groups
