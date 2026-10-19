from __future__ import annotations

import json

from massacre_tracker.domain.eligibility import build_mission, is_eligible_mission
from massacre_tracker.domain.models import StationContext
from tests.builders import BASE_TIME, iso, mission_accepted

STATION = StationContext(system="Sol", system_address=1, station="Galileo")


def payload(**overrides):
    data = json.loads(mission_accepted(1))
    data.update(overrides)
    return data


def test_eligible_mission_uses_station_snapshot():
    mission = build_mission(payload(), BASE_TIME, STATION, True)
    assert mission is not None
    assert mission.source_system == "Sol"
    assert mission.source_station == "Galileo"
    assert mission.contract_kills == 10
    assert mission.progress_kills == 0


def test_legacy_session_is_not_eligible():
    assert not is_eligible_mission(payload(), STATION, False)
    assert build_mission(payload(), BASE_TIME, STATION, False) is None


def test_no_station_is_not_eligible():
    assert not is_eligible_mission(payload(), StationContext(), True)


def test_bad_kill_counts_are_not_eligible():
    assert not is_eligible_mission(payload(KillCount=0), STATION, True)
    missing = payload()
    del missing["KillCount"]
    assert not is_eligible_mission(missing, STATION, True)


def test_empty_target_faction_is_not_eligible():
    assert not is_eligible_mission(payload(TargetFaction=""), STATION, True)
    assert not is_eligible_mission(payload(TargetFaction="   "), STATION, True)


def test_non_pirate_target_type_is_not_eligible():
    assert not is_eligible_mission(
        payload(TargetType="MissionUtil_FactionTag_Civilian"), STATION, True
    )
    assert not is_eligible_mission(payload(TargetType=""), STATION, True)


def test_pirate_tag_is_case_insensitive():
    assert is_eligible_mission(
        payload(TargetType="prefix missionutil_factiontag_pirate suffix"),
        STATION,
        True,
    )


def test_missing_mission_id_does_not_create_mission():
    data = payload()
    del data["MissionID"]
    assert build_mission(data, BASE_TIME, STATION, True) is None


def test_expiry_and_optional_fields():
    expiry = "2026-10-20T12:00:00Z"
    mission = build_mission(payload(Expiry=expiry), BASE_TIME, STATION, True)
    assert mission is not None
    assert mission.expires_at is not None
    assert mission.expires_at.isoformat() == "2026-10-20T12:00:00+00:00"

    no_expiry = payload()
    no_expiry.pop("Expiry", None)
    mission = build_mission(no_expiry, BASE_TIME, STATION, True)
    assert mission is not None
    assert mission.expires_at is None


def test_localised_name_reward_and_wing_are_not_filters():
    data = payload(LocalisedName="anything", Reward=0, Wing=True)
    mission = build_mission(data, BASE_TIME, STATION, True)
    assert mission is not None
    assert mission.wing is True
    assert mission.reward == 0


def test_iso_helper_round_trip():
    assert iso(BASE_TIME).startswith("2026-10-19T12:00:00")
