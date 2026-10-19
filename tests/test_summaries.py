from __future__ import annotations

import json
from datetime import datetime, timezone

from massacre_tracker.domain.lifecycle import (
    apply_mission_accepted,
    apply_mission_redirected,
)
from massacre_tracker.domain.models import (
    CommanderTracker,
    MissionState,
    StationContext,
)
from massacre_tracker.domain.summaries import (
    build_stack_summaries,
    build_target_summaries,
)
from tests.builders import BASE_TIME, at, mission_accepted

STATION = StationContext(system="Sol", system_address=1, station="Galileo")


def add(
    tracker: CommanderTracker,
    mission_id: int,
    *,
    issuer: str,
    target: str = "Target Faction",
    kills: int,
    reward: int = 1000,
    accepted_at: int = 0,
    wing: bool = False,
):
    data = json.loads(
        mission_accepted(
            mission_id,
            faction=issuer,
            target_faction=target,
            kills=kills,
            reward=reward,
            wing=wing,
        )
    )
    mission = apply_mission_accepted(
        tracker, data, at(accepted_at), STATION, True
    )
    assert mission is not None
    return mission


def test_stack_summary_groups_case_insensitively_and_calculates_deficit():
    t = CommanderTracker(key="F", fid="F")
    add(t, 1, issuer="Issuer A", target="Pirates", kills=30, reward=1000)
    add(t, 2, issuer="issuer a", target="pirates", kills=20, reward=2000)
    add(t, 3, issuer="Issuer C", target="PIRATES", kills=25, reward=500)
    redirected = add(t, 4, issuer="Issuer B", target="Pirates", kills=40)
    apply_mission_redirected(t, {"MissionID": 4})

    stacks = {s.issuing_faction: s for s in build_stack_summaries(t)}
    assert set(stacks) == {"Issuer A", "Issuer C"}
    assert stacks["Issuer A"].mission_count == 2
    assert stacks["Issuer A"].contract_kills == 50
    assert stacks["Issuer A"].deficit == 0
    assert stacks["Issuer A"].reward == 3000
    assert stacks["Issuer A"].oldest_first == (1, 2)
    assert stacks["Issuer C"].deficit == 25
    assert redirected.state is MissionState.REDIRECTED


def test_target_summary_matches_spec_example():
    t = CommanderTracker(key="F", fid="F")
    a30 = add(t, 1, issuer="A", kills=30, reward=1000, accepted_at=1)
    a20 = add(t, 2, issuer="A", kills=20, reward=2000, accepted_at=2)
    b40 = add(t, 3, issuer="B", kills=40, reward=3000, accepted_at=3)
    a30.progress_kills = 10
    a20.progress_kills = 5
    b40.progress_kills = 40
    b40.state = MissionState.REDIRECTED

    summaries = build_target_summaries(t)
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.target_faction == "Target Faction"
    assert summary.mission_count == 3
    assert summary.active_mission_count == 2
    assert summary.max_stack_contract == 50
    assert summary.remaining_required == 35
    assert summary.total_contract == 90
    assert summary.overlap_ratio == 1.8
    assert summary.total_reward == 6000
    assert summary.shareable_reward == 0
    assert summary.turn_in_reward == 3000
    assert summary.shareable_turn_in_reward == 0
    assert summary.average_per_unique_kill == 120
    assert summary.average_per_mission == 2000
    assert summary.kills_to_next_completion == 20


def test_target_summary_excludes_terminal_missions_and_handles_zeroes():
    t = CommanderTracker(key="F", fid="F")
    mission = add(t, 1, issuer="A", kills=10)
    mission.state = MissionState.COMPLETED
    assert build_target_summaries(t) == []

    add(t, 2, issuer="A", kills=1)
    t.missions[2].contract_kills = 0
    summary = build_target_summaries(t)[0]
    assert summary.max_stack_contract == 0
    assert summary.overlap_ratio == 0
    assert summary.average_per_unique_kill == 0
    assert summary.kills_to_next_completion == 0


def test_shareable_and_turn_in_rewards():
    t = CommanderTracker(key="F", fid="F")
    add(t, 1, issuer="A", kills=10, reward=1000, wing=True)
    add(t, 2, issuer="A", kills=10, reward=2000, wing=False)
    m = add(t, 3, issuer="B", kills=10, reward=4000, wing=True)
    m.state = MissionState.REDIRECTED
    m.progress_kills = m.contract_kills

    summary = build_target_summaries(t)[0]
    assert summary.total_reward == 7000
    assert summary.shareable_reward == 5000
    assert summary.turn_in_reward == 4000
    assert summary.shareable_turn_in_reward == 4000


def test_ready_state_is_derived_from_station_name_case_insensitively():
    t = CommanderTracker(key="F", fid="F")
    mission = add(t, 1, issuer="A", kills=1)
    mission.state = MissionState.REDIRECTED
    mission.progress_kills = 1
    assert mission.is_ready_to_turn_in({"galileo"})
    assert not mission.is_ready_to_turn_in({"Sol"})
    mission.state = MissionState.ACTIVE
    assert not mission.is_ready_to_turn_in({"galileo"})


def test_unknown_expiry_is_preserved():
    t = CommanderTracker(key="F", fid="F")
    m = add(t, 1, issuer="A", kills=1)
    assert m.expires_at is None
    assert isinstance(BASE_TIME, datetime)
    assert BASE_TIME.tzinfo == timezone.utc
