from __future__ import annotations

import json

from massacre_tracker.domain.lifecycle import (
    apply_mission_abandoned,
    apply_mission_accepted,
    apply_mission_completed,
    apply_mission_failed,
    apply_mission_redirected,
    reconcile_ignored_missions,
    reconcile_missions,
    record_ignored_mission,
    set_manual_state,
)
from massacre_tracker.domain.models import (
    CommanderTracker,
    MissionState,
    StationContext,
)
from tests.builders import BASE_TIME, mission_accepted

STATION = StationContext(system="Sol", system_address=1, station="Galileo")


def new_tracker() -> CommanderTracker:
    return CommanderTracker(key="F100", fid="F100")


def accepted(tracker, mission_id=1, **overrides):
    data = json.loads(mission_accepted(mission_id, **overrides))
    mission = apply_mission_accepted(tracker, data, BASE_TIME, STATION, True)
    assert mission is not None
    return mission


def test_accept_is_idempotent():
    tracker = new_tracker()
    accepted(tracker)
    data = json.loads(mission_accepted(1, faction="Different"))
    assert apply_mission_accepted(tracker, data, BASE_TIME, STATION, True) is None
    assert len(tracker.missions) == 1
    assert tracker.missions[1].issuing_faction == "Issuer A"


def test_ineligible_accept_is_ignored():
    tracker = new_tracker()
    data = json.loads(mission_accepted(1, target_type="NotPirate"))
    assert apply_mission_accepted(tracker, data, BASE_TIME, STATION, True) is None
    assert tracker.missions == {}


def test_smuggler_accept_is_remembered_as_ignored():
    tracker = new_tracker()
    data = json.loads(
        mission_accepted(
            7,
            target_type="$MissionUtil_FactionTag_Smuggler;",
            localised_name="Kill Target Faction Smugglers",
            reward=5000,
            kills=30,
        )
    )
    data["TargetType_Localised"] = "Smugglers"
    ignored = record_ignored_mission(tracker, data, BASE_TIME, STATION)
    assert ignored is not None
    assert ignored.mission_id == 7
    assert ignored.source_station == "Galileo"
    assert ignored.target_type_label == "Smugglers"
    assert ignored.contract_kills == 30
    assert ignored.reward == 5000
    assert tracker.missions == {}
    assert tracker.ignored_missions[7] is ignored


def test_non_smuggler_is_not_remembered_as_ignored():
    tracker = new_tracker()
    data = json.loads(mission_accepted(1, target_type="NotPirate"))
    assert record_ignored_mission(tracker, data, BASE_TIME, STATION) is None
    assert tracker.ignored_missions == {}


def test_duplicate_ignored_mission_is_idempotent():
    tracker = new_tracker()
    data = json.loads(
        mission_accepted(7, target_type="$MissionUtil_FactionTag_Smuggler;")
    )
    assert record_ignored_mission(tracker, data, BASE_TIME, STATION) is not None
    assert record_ignored_mission(tracker, data, BASE_TIME, STATION) is None
    assert set(tracker.ignored_missions) == {7}


def test_terminal_events_clear_ignored_mission():
    tracker = new_tracker()
    data = json.loads(
        mission_accepted(7, target_type="$MissionUtil_FactionTag_Smuggler;")
    )
    for event in (
        apply_mission_completed,
        apply_mission_abandoned,
        apply_mission_failed,
    ):
        tracker.ignored_missions.clear()
        record_ignored_mission(tracker, data, BASE_TIME, STATION)
        event(tracker, {"MissionID": 7})
        assert tracker.ignored_missions == {}


def test_reconciliation_prunes_unlisted_ignored_missions_only():
    tracker = new_tracker()
    data = json.loads(
        mission_accepted(7, target_type="$MissionUtil_FactionTag_Smuggler;")
    )
    record_ignored_mission(tracker, data, BASE_TIME, STATION)
    data = json.loads(
        mission_accepted(8, target_type="$MissionUtil_FactionTag_Smuggler;")
    )
    record_ignored_mission(tracker, data, BASE_TIME, STATION)
    snapshot = {"Active": [{"MissionID": 7}], "Complete": [], "Failed": []}
    assert reconcile_ignored_missions(tracker, snapshot) == 1
    assert set(tracker.ignored_missions) == {7}


def test_redirected_fills_progress_and_is_idempotent():
    tracker = new_tracker()
    accepted(tracker)
    data = {"MissionID": 1}
    assert apply_mission_redirected(tracker, data)
    mission = tracker.missions[1]
    assert mission.state is MissionState.REDIRECTED
    assert mission.progress_kills == mission.contract_kills
    assert apply_mission_redirected(tracker, data)
    assert mission.progress_kills == mission.contract_kills


def test_completed_uses_event_reward():
    tracker = new_tracker()
    accepted(tracker)
    assert apply_mission_completed(tracker, {"MissionID": 1, "Reward": 4321})
    mission = tracker.missions[1]
    assert mission.state is MissionState.COMPLETED
    assert mission.reward == 4321


def test_abandoned_and_failed_zero_reward():
    tracker = new_tracker()
    accepted(tracker, 1)
    accepted(tracker, 2)
    assert apply_mission_abandoned(tracker, {"MissionID": 1})
    assert apply_mission_failed(tracker, {"MissionID": 2})
    assert tracker.missions[1].state is MissionState.ABANDONED
    assert tracker.missions[1].reward == 0
    assert tracker.missions[2].state is MissionState.FAILED
    assert tracker.missions[2].reward == 0


def test_missing_mission_events_are_harmless():
    tracker = new_tracker()
    assert not apply_mission_redirected(tracker, {"MissionID": 99})
    assert not apply_mission_completed(tracker, {"MissionID": 99})
    assert not apply_mission_abandoned(tracker, {"MissionID": 99})
    assert not apply_mission_failed(tracker, {"MissionID": 99})


def test_reconciliation_completes_unlisted_existing_missions_only():
    tracker = new_tracker()
    accepted(tracker, 1)
    accepted(tracker, 2)
    snapshot = {
        "Active": [{"MissionID": 1}],
        "Complete": [],
        "Failed": [],
    }
    assert reconcile_missions(tracker, snapshot) == 1
    assert tracker.missions[1].state is MissionState.ACTIVE
    assert tracker.missions[2].state is MissionState.COMPLETED
    assert set(tracker.missions) == {1, 2}


def test_reconciliation_does_not_create_missions():
    tracker = new_tracker()
    reconcile_missions(tracker, {"Active": [{"MissionID": 5}]})
    assert tracker.missions == {}


def test_reconciliation_turns_unlisted_abandoned_into_completed_per_spec():
    tracker = new_tracker()
    accepted(tracker, 1)
    apply_mission_abandoned(tracker, {"MissionID": 1})
    reconcile_missions(tracker, {"Active": [], "Complete": [], "Failed": []})
    assert tracker.missions[1].state is MissionState.COMPLETED


def test_manual_state_change_and_reward_rules():
    tracker = new_tracker()
    accepted(tracker)
    assert set_manual_state(tracker, 1, MissionState.FAILED)
    assert tracker.missions[1].reward == 0
    assert set_manual_state(tracker, 1, MissionState.ACTIVE)
    assert tracker.missions[1].state is MissionState.ACTIVE
    assert not set_manual_state(tracker, 99, MissionState.COMPLETED)
