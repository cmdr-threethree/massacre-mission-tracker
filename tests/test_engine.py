from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from massacre_tracker.domain.models import MissionState, StationContext
from massacre_tracker.engine import TrackerEngine
from massacre_tracker.journal.events import EventEnvelope
from tests.builders import BASE_TIME, at
from tests.helpers import envelope, mission_payload

SOL = StationContext(system="Sol", system_address=1, station="Galileo")
LHS = StationContext(system="LHS 20", system_address=2, station="Fisher")


def base_events(
    path: Path = Path("Journal.2026-10-19T120000.01.log"),
    *,
    fid: str = "F100",
    name: str = "Jameson",
    station: StationContext = SOL,
    version: str = "4.0.0.100",
) -> list[EventEnvelope]:
    return [
        envelope("Fileheader", {"gameversion": version}, path=path, line_number=0),
        envelope("Commander", {"FID": fid, "Name": name}, path=path, line_number=1),
        envelope(
            "Docked",
            {
                "StarSystem": station.system,
                "SystemAddress": station.system_address,
                "StationName": station.station,
            },
            path=path,
            line_number=2,
        ),
    ]


def mission_envelope(
    mission_id: int = 1,
    *,
    path: Path = Path("Journal.2026-10-19T120000.01.log"),
    line_number: int = 3,
    station: StationContext = SOL,
    timestamp=BASE_TIME,
    **payload,
) -> EventEnvelope:
    return envelope(
        "MissionAccepted",
        mission_payload(mission_id, **payload),
        path=path,
        line_number=line_number,
        station=station,
        timestamp=timestamp,
    )


def test_full_session_creates_tracked_mission():
    engine = TrackerEngine()
    events = base_events() + [mission_envelope(station=SOL)]
    assert engine.apply_batch(events) == 4
    view = engine.commander_view("F100")
    assert view is not None
    assert view.display_name == "Jameson"
    assert len(view.missions) == 1
    mission = view.missions[0]
    assert mission.source_system == "Sol"
    assert mission.source_station == "Galileo"
    assert mission.state is MissionState.ACTIVE


def test_legacy_session_cannot_create_missions():
    engine = TrackerEngine()
    events = base_events(version="3.8.0.0") + [mission_envelope()]
    engine.apply_batch(events)
    view = engine.commander_view("F100")
    assert view is not None
    assert view.missions == ()
    session = engine.session_for(Path("Journal.2026-10-19T120000.01.log"))
    assert session is not None
    assert session.live is False


def test_missing_fileheader_is_treated_as_live_at_replay_end():
    engine = TrackerEngine()
    path = Path("Journal.2026-10-19T120000.01.log")
    events = [
        envelope("Docked", {"Docked": True, "StationName": "Galileo", "StarSystem": "Sol"}, path=path, line_number=0),
        envelope("Commander", {"FID": "F100", "Name": "Jameson"}, path=path, line_number=1),
        mission_envelope(path=path, line_number=2),
    ]
    engine.apply_batch(events)
    engine.resolve_unknown_sessions(now=BASE_TIME)
    view = engine.commander_view("F100")
    assert view is not None
    assert len(view.missions) == 1
    session = engine.session_for(path)
    assert session is not None
    assert session.live is True


def test_events_before_commander_are_buffered_and_migrated():
    engine = TrackerEngine()
    path = Path("Journal.2026-10-19T120000.01.log")
    events = [
        envelope("Fileheader", {"gameversion": "4.0.0.100"}, path=path, line_number=0),
        envelope(
            "Docked",
            {"Docked": True, "StarSystem": "Sol", "StationName": "Galileo"},
            path=path,
            line_number=1,
        ),
        mission_envelope(path=path, line_number=2),
    ]
    engine.apply_batch(events)
    assert engine.commander_keys() == []
    session = engine.session_for(path)
    assert session is not None
    assert len(session.pending) == 1

    resolved = engine.resolve_unknown_sessions(now=BASE_TIME)
    assert resolved
    unknown_keys = engine.commander_keys()
    assert len(unknown_keys) == 1
    assert unknown_keys[0].endswith(path.name)
    unknown_view = engine.commander_view(unknown_keys[0])
    assert unknown_view is not None
    assert len(unknown_view.missions) == 1

    engine.apply_batch(
        [
            envelope(
                "Commander",
                {"FID": "F100", "Name": "Jameson"},
                path=path,
                line_number=3,
                timestamp=BASE_TIME + timedelta(seconds=1),
            )
        ]
    )
    assert engine.commander_keys() == ["F100"]
    view = engine.commander_view("F100")
    assert view is not None
    assert len(view.missions) == 1


def test_station_snapshot_survives_later_undock_and_redock():
    engine = TrackerEngine()
    path = Path("Journal.2026-10-19T120000.01.log")
    events = base_events(path) + [
        mission_envelope(path=path, line_number=3, station=SOL),
        envelope("Undocked", {}, path=path, line_number=4, timestamp=at(1)),
        envelope(
            "Docked",
            {"StarSystem": "LHS 20", "StationName": "Fisher"},
            path=path,
            line_number=5,
            timestamp=at(2),
        ),
    ]
    engine.apply_batch(events)
    view = engine.commander_view("F100")
    assert view is not None
    assert view.missions[0].source_station == "Galileo"
    assert view.station_names == frozenset({"fisher"})


def test_same_commander_two_sessions_share_missions_not_stations():
    engine = TrackerEngine()
    path_a = Path("Journal.2026-10-19T120000.01.log")
    path_b = Path("Journal.2026-10-19T120001.01.log")
    events = base_events(path_a, station=SOL) + base_events(path_b, station=LHS)
    events += [
        mission_envelope(1, path=path_a, line_number=3, station=SOL),
        mission_envelope(2, path=path_b, line_number=3, station=LHS),
    ]
    engine.apply_batch(events)
    view = engine.commander_view("F100")
    assert view is not None
    assert len(view.missions) == 2
    sources = {m.source_station for m in view.missions}
    assert sources == {"Galileo", "Fisher"}
    assert view.station_names == frozenset({"galileo", "fisher"})
    assert len(view.sessions) == 2


def test_different_commanders_are_isolated():
    engine = TrackerEngine()
    path_a = Path("Journal.2026-10-19T120000.01.log")
    path_b = Path("Journal.2026-10-19T120001.01.log")
    events = base_events(path_a, fid="F100", name="A", station=SOL)
    events += base_events(path_b, fid="F200", name="B", station=LHS)
    events += [
        mission_envelope(1, path=path_a, line_number=3, station=SOL),
        mission_envelope(2, path=path_b, line_number=3, station=LHS, faction="Other"),
    ]
    engine.apply_batch(events)
    view_a = engine.commander_view("F100")
    view_b = engine.commander_view("F200")
    assert view_a is not None and view_b is not None
    assert [m.mission_id for m in view_a.missions] == [1]
    assert [m.mission_id for m in view_b.missions] == [2]
    assert view_a.bounties == ()
    assert view_b.bounties == ()


def test_duplicate_line_hash_is_not_applied_twice():
    engine = TrackerEngine()
    events = base_events() + [mission_envelope()]
    assert engine.apply_batch(events) == 4
    assert engine.apply_batch(events) == 0
    view = engine.commander_view("F100")
    assert view is not None
    assert len(view.missions) == 1
    assert engine.stats().events_processed == 4


def test_bounty_credits_only_session_commander():
    engine = TrackerEngine()
    path_a = Path("Journal.2026-10-19T120000.01.log")
    path_b = Path("Journal.2026-10-19T120001.01.log")
    events = base_events(path_a, fid="F100", station=SOL)
    events += base_events(path_b, fid="F200", station=SOL)
    events += [
        mission_envelope(1, path=path_a, line_number=3, station=SOL),
        envelope(
            "Bounty",
            {"Target": "sidewinder", "VictimFaction": "Target Faction", "TotalReward": 100},
            path=path_a,
            line_number=4,
            timestamp=at(1),
        ),
    ]
    engine.apply_batch(events)
    assert engine.commander_view("F100").missions[0].progress_kills == 1
    assert engine.commander_view("F200").missions == ()


def test_manual_actions_and_clear_completed():
    engine = TrackerEngine()
    engine.apply_batch(base_events() + [mission_envelope()])
    assert engine.adjust_mission("F100", 1, 1)
    assert engine.commander_view("F100").missions[0].progress_kills == 1
    assert engine.set_mission_state("F100", 1, MissionState.COMPLETED)
    assert engine.clear_completed("F100") == 1
    assert engine.commander_view("F100").missions == ()
    assert not engine.adjust_mission("missing", 1, 1)


def test_location_undocked_does_not_change_station_but_undocked_clears():
    engine = TrackerEngine()
    path = Path("Journal.2026-10-19T120000.01.log")
    events = base_events(path)
    events.append(
        envelope(
            "Location",
            {
                "Docked": False,
                "StarSystem": "LHS 20",
                "SystemAddress": 2,
                "StationName": "Fisher",
            },
            path=path,
            line_number=3,
            timestamp=at(1),
        )
    )
    events.append(
        envelope("Undocked", {}, path=path, line_number=4, timestamp=at(2))
    )
    engine.apply_batch(events)
    session = engine.session_for(path)
    assert session is not None
    assert session.station is None


def test_later_commander_in_same_session_keeps_old_commander_state():
    engine = TrackerEngine()
    path = Path("Journal.2026-10-19T120000.01.log")
    events = base_events(path, fid="F100", name="First")
    events.append(mission_envelope(1, path=path, line_number=3))
    events.append(
        envelope(
            "Commander",
            {"FID": "F200", "Name": "Second"},
            path=path,
            line_number=4,
            timestamp=at(1),
        )
    )
    events.append(mission_envelope(2, path=path, line_number=5, timestamp=at(2)))
    engine.apply_batch(events)

    first = engine.commander_view("F100")
    second = engine.commander_view("F200")
    assert first is not None and second is not None
    assert [m.mission_id for m in first.missions] == [1]
    assert [m.mission_id for m in second.missions] == [2]
    session = engine.session_for(path)
    assert session is not None
    assert session.commander_fid == "F200"


def test_commander_display_name_never_falls_back_to_fid():
    engine = TrackerEngine()
    path = Path("Journal.2026-10-19T120000.01.log")
    events = [
        envelope("Fileheader", {"gameversion": "4.0.0.100"}, path=path, line_number=0),
        envelope("Commander", {"FID": "F100", "Name": ""}, path=path, line_number=1),
    ]
    engine.apply_batch(events)
    view = engine.commander_view("F100")
    assert view is not None
    assert view.display_name == "Unknown commander"
    assert view.display_name != "F100"


def test_stats_and_warnings():
    engine = TrackerEngine()
    engine.note_malformed(3)
    engine.add_warning("problem")
    engine.add_warning("problem")
    stats = engine.stats()
    assert stats.malformed_lines == 3
    assert stats.warnings == ("problem",)
