from __future__ import annotations

import json

from massacre_tracker.domain.eligibility import (
    build_mission,
    is_eligible_mission,
    is_smuggler_target,
)
from massacre_tracker.domain.models import MissionKind, StationContext
from tests.builders import BASE_TIME, iso, mining_mission_accepted, mission_accepted

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


def test_smuggler_target_is_recognised_but_not_eligible():
    data = payload(
        TargetType="$MissionUtil_FactionTag_Smuggler;",
        TargetType_Localised="Smugglers",
    )
    assert is_smuggler_target(data)
    assert not is_eligible_mission(data, STATION, True)
    assert build_mission(data, BASE_TIME, STATION, True) is None


def test_smuggler_tag_is_case_insensitive():
    assert is_smuggler_target(
        payload(TargetType="prefix missionutil_factiontag_smuggler suffix")
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


def mining_payload(**overrides):
    data = json.loads(mining_mission_accepted(1))
    data.update(overrides)
    return data


def test_mining_mission_is_eligible_and_built():
    data = mining_payload()
    assert is_eligible_mission(data, STATION, True)
    mission = build_mission(data, BASE_TIME, STATION, True)
    assert mission is not None
    assert mission.kind is MissionKind.MINING
    assert mission.commodity == "Bertrandite"
    assert mission.required_amount == 100
    assert mission.destination_station == "Destination Station"
    assert mission.target_faction == ""


def test_mining_mission_requires_station_live_commodity_and_count():
    data = mining_payload()
    assert not is_eligible_mission(data, StationContext(), True)
    assert not is_eligible_mission(data, STATION, False)
    assert not is_eligible_mission(mining_payload(Count=0), STATION, True)
    no_commodity = data.copy()
    del no_commodity["Commodity"]
    del no_commodity["Commodity_Localised"]
    assert not is_eligible_mission(no_commodity, STATION, True)


def test_mining_name_prefix_is_required():
    assert not is_eligible_mission(
        mining_payload(Name="Mission_Delivery"), STATION, True
    )
