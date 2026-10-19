from __future__ import annotations

import json

from massacre_tracker.domain.kills import (
    adjust_all_stacks,
    adjust_mission,
    apply_bounty,
    bounty_is_eligible,
)
from massacre_tracker.domain.models import (
    CommanderTracker,
    MissionState,
    StationContext,
)
from tests.builders import at, bounty, mission_accepted

STATION = StationContext(system="Sol", system_address=1, station="Galileo")


def tracker() -> CommanderTracker:
    return CommanderTracker(key="F100", fid="F100")


def add(
    t: CommanderTracker,
    mission_id: int,
    *,
    issuer: str = "Issuer A",
    target: str = "Target Faction",
    kills: int = 10,
    accepted_at=0,
    reward: int = 1000,
):
    from massacre_tracker.domain.lifecycle import apply_mission_accepted

    data = json.loads(
        mission_accepted(
            mission_id,
            faction=issuer,
            target_faction=target,
            kills=kills,
            reward=reward,
        )
    )
    mission = apply_mission_accepted(
        t, data, at(accepted_at), STATION, True
    )
    assert mission is not None
    return mission


def credit(t: CommanderTracker, victim="Target Faction", timestamp=100):
    data = json.loads(bounty(victim_faction=victim))
    return apply_bounty(t, data, at(timestamp))


def test_one_bounty_credits_only_oldest_mission_in_stack():
    t = tracker()
    older = add(t, 1, accepted_at=10)
    newer = add(t, 2, accepted_at=20)
    assert credit(t)
    assert older.progress_kills == 1
    assert newer.progress_kills == 0


def test_one_bounty_credits_one_mission_per_stack():
    t = tracker()
    a = add(t, 1, issuer="Issuer A", accepted_at=10)
    b = add(t, 2, issuer="Issuer B", accepted_at=20)
    assert credit(t)
    assert a.progress_kills == 1
    assert b.progress_kills == 1


def test_target_match_is_case_insensitive():
    t = tracker()
    m = add(t, 1, target="target FACTION")
    assert credit(t, victim="TARGET faction")
    assert m.progress_kills == 1


def test_full_mission_is_redirected_and_does_not_block_next():
    t = tracker()
    first = add(t, 1, kills=1, accepted_at=10)
    second = add(t, 2, kills=5, accepted_at=20)
    assert credit(t)
    assert first.progress_kills == 1
    assert first.state is MissionState.REDIRECTED
    assert credit(t, timestamp=101)
    assert second.progress_kills == 1


def test_redirected_mission_is_not_credited_again():
    t = tracker()
    m = add(t, 1, kills=1)
    credit(t)
    credit(t, timestamp=101)
    assert m.progress_kills == 1


def test_bounty_filters_are_exact_and_case_sensitive():
    assert not bounty_is_eligible(
        {"VictimFaction": "faction_none", "Target": "sidewinder", "TotalReward": 10}
    )
    assert not bounty_is_eligible(
        {"VictimFaction": "faction_Pirate", "Target": "sidewinder", "TotalReward": 10}
    )
    assert not bounty_is_eligible(
        {"VictimFaction": "Pirates", "Target": "suit_something", "TotalReward": 10}
    )
    assert not bounty_is_eligible(
        {"VictimFaction": "Pirates", "Target": "sidewinder", "TotalReward": 0}
    )
    assert not bounty_is_eligible(
        {"VictimFaction": "Pirates", "Target": "sidewinder", "TotalReward": -5}
    )
    assert bounty_is_eligible(
        {"VictimFaction": "Faction_None", "Target": "Suit", "TotalReward": 10}
    )


def test_ineligible_bounty_does_not_progress_or_feed():
    t = tracker()
    m = add(t, 1)
    data = {
        "VictimFaction": "faction_none",
        "Target": "sidewinder",
        "TotalReward": 100,
    }
    assert not apply_bounty(t, data, at(100))
    assert m.progress_kills == 0
    assert len(t.bounty_feed) == 0


def test_eligible_bounty_enters_feed_even_without_matching_stack():
    t = tracker()
    credit(t, victim="Someone Else")
    assert len(t.bounty_feed) == 1


def test_manual_selected_adjustment_clamps_and_changes_state():
    t = tracker()
    m = add(t, 1, kills=1)
    assert adjust_mission(t, 1, 1)
    assert m.progress_kills == 1
    assert m.state is MissionState.REDIRECTED
    assert adjust_mission(t, 1, 1)
    assert m.progress_kills == 1
    assert adjust_mission(t, 1, -1)
    assert m.progress_kills == 0
    assert m.state is MissionState.ACTIVE
    assert adjust_mission(t, 1, -5)
    assert m.progress_kills == 0


def test_manual_all_plus_one_considers_each_pair_once():
    t = tracker()
    a1 = add(t, 1, issuer="A", accepted_at=10)
    a2 = add(t, 2, issuer="A", accepted_at=20)
    b1 = add(t, 3, issuer="B", accepted_at=5)
    assert adjust_all_stacks(t, 1) == 2
    assert a1.progress_kills == 1
    assert a2.progress_kills == 0
    assert b1.progress_kills == 1


def test_manual_all_minus_one_uses_newest_and_reactivates():
    t = tracker()
    older = add(t, 1, issuer="A", accepted_at=10, kills=2)
    newer = add(t, 2, issuer="A", accepted_at=20, kills=2)
    adjust_mission(t, 2, 1)
    older.progress_kills = older.contract_kills
    older.state = MissionState.REDIRECTED

    assert adjust_all_stacks(t, -1) == 1
    assert newer.progress_kills == 0
    assert older.progress_kills == 2

    assert adjust_all_stacks(t, -1) == 1
    assert older.progress_kills == 1
    assert older.state is MissionState.ACTIVE


def test_bounty_feed_keeps_last_twenty_with_deltas():
    t = tracker()
    for index in range(25):
        data = json.loads(bounty(timestamp=at(index * 10), reward=100 + index))
        apply_bounty(t, data, at(index * 10))
    assert len(t.bounty_feed) == 20
    assert t.bounty_feed[-1].reward == 124
    assert t.bounty_feed[-1].delta_since_previous == 10
    assert t.bounty_feed[0].reward == 105
