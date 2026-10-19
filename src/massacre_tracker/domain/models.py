"""Core domain objects for massacre mission tracking."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

class MissionState(StrEnum):
    ACTIVE = "ACTIVE"
    REDIRECTED = "REDIRECTED"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"
    FAILED = "FAILED"


TERMINAL_STATES = frozenset(
    {MissionState.COMPLETED, MissionState.ABANDONED, MissionState.FAILED}
)


@dataclass(frozen=True, slots=True)
class StationContext:
    """A snapshot of the station at the moment an event was read."""

    system: str | None = None
    system_address: int | None = None
    station: str | None = None


@dataclass(slots=True)
class Mission:
    mission_id: int
    source_system: str | None
    system_address: int | None
    source_station: str | None
    issuing_faction: str
    destination_system: str | None
    target_faction: str
    localised_name: str | None
    reward: int
    contract_kills: int
    progress_kills: int
    wing: bool
    accepted_at: datetime
    expires_at: datetime | None
    state: MissionState = MissionState.ACTIVE

    @property
    def remaining_kills(self) -> int:
        return max(0, self.contract_kills - self.progress_kills)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_stack_member(self) -> bool:
        return self.state == MissionState.ACTIVE

    @property
    def is_summary_member(self) -> bool:
        return self.state in (MissionState.ACTIVE, MissionState.REDIRECTED)

    @property
    def sort_key(self) -> tuple[datetime, int]:
        return (self.accepted_at, self.mission_id)

    def clamp_progress(self) -> None:
        self.progress_kills = max(
            0, min(self.progress_kills, self.contract_kills)
        )

    def set_progress(self, value: int) -> None:
        self.progress_kills = max(0, min(int(value), self.contract_kills))

    def is_ready_to_turn_in(self, station_names: set[str]) -> bool:
        """Ready state is derived from the current session stations."""

        if self.state != MissionState.REDIRECTED or not self.source_station:
            return False
        return self.source_station.casefold() in station_names

    def copy(self) -> Mission:
        return replace(self)


@dataclass(slots=True)
class Bounty:
    timestamp: datetime
    ship_type: str
    victim_faction: str
    reward: int
    delta_since_previous: float | None = None


@dataclass(slots=True)
class CommanderTracker:
    """All mission and bounty state belonging to one commander."""

    key: str
    fid: str | None = None
    name: str | None = None
    missions: dict[int, Mission] = field(default_factory=dict)
    bounty_feed: deque[Bounty] = field(default_factory=lambda: deque(maxlen=20))

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.fid:
            return self.fid
        return self.key

    def add_mission(self, mission: Mission) -> bool:
        """Add a mission, returning False when the ID already exists."""

        if mission.mission_id in self.missions:
            return False
        self.missions[mission.mission_id] = mission
        return True

    def get_mission(self, mission_id: int) -> Mission | None:
        return self.missions.get(mission_id)

    def remove_mission(self, mission_id: int) -> bool:
        return self.missions.pop(mission_id, None) is not None

    def clear_terminal(self) -> int:
        ids = [mid for mid, m in self.missions.items() if m.is_terminal]
        for mid in ids:
            del self.missions[mid]
        return len(ids)

    def record_bounty(self, bounty: Bounty) -> Bounty:
        if self.bounty_feed:
            previous = self.bounty_feed[-1]
            bounty.delta_since_previous = max(
                0.0, (bounty.timestamp - previous.timestamp).total_seconds()
            )
        else:
            bounty.delta_since_previous = None
        self.bounty_feed.append(bounty)
        return bounty

    def replace_bounty_feed(self, bounties: list[Bounty]) -> None:
        """Replace the feed, sorting and recomputing deltas."""

        ordered = sorted(bounties, key=lambda b: (b.timestamp, b.ship_type))
        retained = ordered[-20:]
        self.bounty_feed.clear()
        previous: Bounty | None = None
        for bounty in retained:
            if previous is None:
                bounty.delta_since_previous = None
            else:
                bounty.delta_since_previous = max(
                    0.0, (bounty.timestamp - previous.timestamp).total_seconds()
                )
            previous = bounty
            self.bounty_feed.append(bounty)
