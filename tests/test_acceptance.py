from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from massacre_tracker.config import Config
from massacre_tracker.domain.kills import apply_bounty, bounty_is_eligible
from massacre_tracker.domain.lifecycle import (
    apply_mission_abandoned,
    apply_mission_accepted,
    apply_mission_completed,
    apply_mission_failed,
    apply_mission_redirected,
    reconcile_missions,
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
from massacre_tracker.engine import TrackerEngine
from massacre_tracker.journal.scanner import JournalScanner
from massacre_tracker.journal.tailer import JournalTailer
from massacre_tracker.ui.app import TrackerApp
from massacre_tracker.ui.clipboard import ClipboardStore
from tests.builders import (
    BASE_TIME,
    at,
    bounty,
    commander,
    docked,
    fileheader,
    journal_path,
    mission_accepted,
    mission_event,
    write_journal,
)
from tests.helpers import mission_payload

STATION = StationContext(system="Sol", system_address=1, station="Galileo")


def set_mtime(path: Path, dt: datetime) -> None:
    seconds = dt.timestamp()
    os.utime(path, (seconds, seconds))


def scan(directory: Path, *, now: datetime = BASE_TIME) -> tuple[TrackerEngine, Config]:
    engine = TrackerEngine()
    config = Config(journal_dir=directory)
    JournalScanner(engine, config, clock=lambda: now).scan()
    return engine, config


def add_mission(
    tracker: CommanderTracker,
    mission_id: int,
    *,
    issuer: str = "Issuer A",
    target: str = "Target Faction",
    kills: int = 10,
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
    mission = apply_mission_accepted(tracker, data, at(accepted_at), STATION, True)
    assert mission is not None
    return mission


def test_acceptance_01_eight_day_window(tmp_path: Path):
    old = journal_path(tmp_path, BASE_TIME - timedelta(days=10), 1)
    write_journal(
        old,
        [fileheader(BASE_TIME - timedelta(days=10)), commander(), docked(), mission_accepted(1)],
    )
    set_mtime(old, BASE_TIME - timedelta(days=10))
    recent = journal_path(tmp_path, BASE_TIME - timedelta(days=1), 2)
    write_journal(
        recent,
        [fileheader(BASE_TIME - timedelta(days=1)), commander(), docked(), mission_accepted(2)],
    )
    set_mtime(recent, BASE_TIME - timedelta(days=1))

    engine, _ = scan(tmp_path)
    assert engine.stats().watched_files == 2
    assert engine.session_for(old).offset == old.stat().st_size
    assert engine.session_for(old).line_number == 0
    assert engine.commander_keys() == ["F100"]
    assert [m.mission_id for m in engine.commander_view("F100").missions] == [2]


def test_acceptance_02_long_running_session(tmp_path: Path):
    path = journal_path(tmp_path, BASE_TIME - timedelta(days=9), 1)
    old = BASE_TIME - timedelta(days=9)
    write_journal(
        path,
        [
            fileheader(old),
            commander(timestamp=old),
            docked(timestamp=old),
            mission_accepted(1, timestamp=old),
            mission_accepted(2, timestamp=BASE_TIME - timedelta(days=1)),
        ],
    )
    set_mtime(path, BASE_TIME)
    engine, _ = scan(tmp_path)
    assert [m.mission_id for m in engine.commander_view("F100").missions] == [2]


def test_acceptance_03_eligible_mission(tmp_path: Path):
    path = journal_path(tmp_path, BASE_TIME, 1)
    write_journal(
        path,
        [fileheader(), commander(), docked(), mission_accepted(1)],
    )
    engine, _ = scan(tmp_path)
    mission = engine.commander_view("F100").missions[0]
    assert mission.state is MissionState.ACTIVE
    assert mission.source_station == "Galileo"
    assert mission.source_system == "Sol"


def test_acceptance_04_ineligible_missions():
    from massacre_tracker.domain.eligibility import is_eligible_mission

    data = mission_payload(1)
    assert not is_eligible_mission(data, StationContext(), True)
    assert not is_eligible_mission({**data, "KillCount": 0}, STATION, True)
    assert not is_eligible_mission({**data, "TargetFaction": ""}, STATION, True)
    assert not is_eligible_mission(
        {**data, "TargetType": "MissionUtil_FactionTag_Trader"}, STATION, True
    )
    assert not is_eligible_mission(data, STATION, False)


def test_acceptance_05_kill_attribution_within_stack():
    tracker = CommanderTracker(key="F", fid="F")
    older = add_mission(tracker, 1, accepted_at=10)
    newer = add_mission(tracker, 2, accepted_at=20)
    apply_bounty(
        tracker,
        {"Target": "sidewinder", "VictimFaction": "Target Faction", "TotalReward": 100},
        at(30),
    )
    assert older.progress_kills == 1
    assert newer.progress_kills == 0


def test_acceptance_06_kill_attribution_across_stacks():
    tracker = CommanderTracker(key="F", fid="F")
    a = add_mission(tracker, 1, issuer="A", accepted_at=10)
    b = add_mission(tracker, 2, issuer="B", accepted_at=20)
    apply_bounty(
        tracker,
        {"Target": "sidewinder", "VictimFaction": "Target Faction", "TotalReward": 100},
        at(30),
    )
    assert a.progress_kills == 1
    assert b.progress_kills == 1


def test_acceptance_07_automatic_ready_state():
    tracker = CommanderTracker(key="F", fid="F")
    mission = add_mission(tracker, 1, kills=1)
    bounty_data = {
        "Target": "sidewinder",
        "VictimFaction": "Target Faction",
        "TotalReward": 100,
    }
    apply_bounty(tracker, bounty_data, at(10))
    assert mission.progress_kills == 1
    assert mission.state is MissionState.REDIRECTED
    assert build_stack_summaries(tracker) == []
    apply_bounty(tracker, bounty_data, at(20))
    assert mission.progress_kills == 1


def test_acceptance_08_redirect_event():
    tracker = CommanderTracker(key="F", fid="F")
    mission = add_mission(tracker, 1)
    mission.progress_kills = 3
    for _ in range(2):
        assert apply_mission_redirected(tracker, {"MissionID": 1})
        assert mission.progress_kills == mission.contract_kills
        assert mission.state is MissionState.REDIRECTED


def test_acceptance_09_completion_and_failure():
    tracker = CommanderTracker(key="F", fid="F")
    completed = add_mission(tracker, 1)
    abandoned = add_mission(tracker, 2)
    failed = add_mission(tracker, 3, issuer="C")
    apply_mission_completed(tracker, {"MissionID": 1, "Reward": 9999})
    apply_mission_abandoned(tracker, {"MissionID": 2})
    apply_mission_failed(tracker, {"MissionID": 3})
    assert completed.reward == 9999
    assert completed.state is MissionState.COMPLETED
    assert abandoned.reward == failed.reward == 0
    assert abandoned.state is MissionState.ABANDONED
    assert failed.state is MissionState.FAILED
    assert build_stack_summaries(tracker) == []
    assert all(m.is_terminal for m in tracker.missions.values())


def test_acceptance_10_reconciliation():
    tracker = CommanderTracker(key="F", fid="F")
    a = add_mission(tracker, 1)
    b = add_mission(tracker, 2)
    reconcile_missions(tracker, {"Active": [{"MissionID": 1}], "Complete": [], "Failed": []})
    assert a.state is MissionState.ACTIVE
    assert b.state is MissionState.COMPLETED
    assert len(tracker.missions) == 2


def test_acceptance_11_different_commanders(tmp_path: Path):
    path_a = journal_path(tmp_path, BASE_TIME, 1)
    path_b = journal_path(tmp_path, BASE_TIME, 2)
    write_journal(path_a, [fileheader(), commander(fid="F100", name="A"), docked(), mission_accepted(1)])
    write_journal(
        path_b,
        [fileheader(), commander(fid="F200", name="B"), docked(station="Fisher", system="LHS 20"), mission_accepted(2)],
    )
    engine, _ = scan(tmp_path)
    assert [m.mission_id for m in engine.commander_view("F100").missions] == [1]
    assert [m.mission_id for m in engine.commander_view("F200").missions] == [2]
    assert engine.commander_view("F100").station_names == frozenset({"galileo"})
    assert engine.commander_view("F200").station_names == frozenset({"fisher"})


def test_acceptance_12_same_commander_multiple_files(tmp_path: Path):
    path_a = journal_path(tmp_path, BASE_TIME, 1)
    path_b = journal_path(tmp_path, BASE_TIME, 2)
    write_journal(path_a, [fileheader(), commander(), docked(), mission_accepted(1)])
    write_journal(
        path_b,
        [fileheader(), commander(), docked(station="Fisher", system="LHS 20"), mission_accepted(2)],
    )
    engine, _ = scan(tmp_path)
    view = engine.commander_view("F100")
    assert len(view.missions) == 2
    assert len(view.sessions) == 2
    assert {m.source_station for m in view.missions} == {"Galileo", "Fisher"}
    assert view.station_names == frozenset({"galileo", "fisher"})


def test_acceptance_13_new_file_after_startup(tmp_path: Path):
    engine, config = scan(tmp_path)
    path = journal_path(tmp_path, at(1), 1)
    write_journal(path, [fileheader(), commander(), docked(), mission_accepted(1)])
    tailer = JournalTailer(engine, config, clock=lambda: at(1))
    result = tailer.poll_once(now=at(1))
    assert result.new_files == 1
    assert [m.mission_id for m in engine.commander_view("F100").missions] == [1]


def test_acceptance_14_append_to_old_file(tmp_path: Path):
    path = journal_path(tmp_path, BASE_TIME, 1)
    write_journal(path, [fileheader(), commander(), docked()])
    engine, config = scan(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(mission_accepted(1) + "\n")
    tailer = JournalTailer(engine, config, clock=lambda: at(1))
    assert tailer.poll_once(now=at(1)).events_applied == 1
    assert tailer.poll_once(now=at(2)).events_applied == 0
    assert len(engine.commander_view("F100").missions) == 1


def test_acceptance_15_partial_line(tmp_path: Path):
    path = journal_path(tmp_path, BASE_TIME, 1)
    write_journal(path, [fileheader(), commander(), docked()])
    engine, config = scan(tmp_path)
    tailer = JournalTailer(engine, config, clock=lambda: at(1))
    line = mission_accepted(1) + "\n"
    split = len(line) // 2
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line[:split])
    tailer.poll_once(now=at(1))
    assert engine.commander_view("F100").missions == ()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line[split:])
    tailer.poll_once(now=at(2))
    assert len(engine.commander_view("F100").missions) == 1
    tailer.poll_once(now=at(3))
    assert len(engine.commander_view("F100").missions) == 1


def test_acceptance_16_truncation(tmp_path: Path):
    path = journal_path(tmp_path, BASE_TIME, 1)
    write_journal(path, [fileheader(), commander(), docked(), mission_accepted(1, kills=2)])
    engine, config = scan(tmp_path)
    tailer = JournalTailer(engine, config, clock=lambda: at(1))
    replacement = "\n".join(
        [fileheader(), commander(), docked(), bounty(timestamp=at(2))]
    ) + "\n"
    path.write_text(replacement, encoding="utf-8")
    result = tailer.poll_once(now=at(2))
    assert result.truncated_files == 1
    assert engine.commander_view("F100").missions[0].progress_kills == 1


def test_acceptance_17_bounty_filters():
    cases = [
        {"VictimFaction": "faction_none", "Target": "sidewinder", "TotalReward": 10},
        {"VictimFaction": "faction_Pirate", "Target": "sidewinder", "TotalReward": 10},
        {"VictimFaction": "Pirates", "Target": "suit", "TotalReward": 10},
        {"VictimFaction": "Pirates", "Target": "sidewinder", "TotalReward": 0},
        {"VictimFaction": "Pirates", "Target": "sidewinder", "TotalReward": -10},
    ]
    assert all(not bounty_is_eligible(case) for case in cases)

    tracker = CommanderTracker(key="F", fid="F")
    mission = add_mission(tracker, 1)
    for case in cases:
        apply_bounty(tracker, case, at(10))
    assert mission.progress_kills == 0
    assert len(tracker.bounty_feed) == 0


def test_acceptance_18_stateless_restart(tmp_path: Path):
    path = journal_path(tmp_path, BASE_TIME, 1)
    write_journal(
        path,
        [
            fileheader(),
            commander(),
            docked(),
            mission_accepted(1, kills=3),
            mission_event("MissionRedirected", 1, timestamp=at(5)),
            mission_accepted(2),
            mission_event("MissionCompleted", 2, timestamp=at(6), Reward=5000),
        ],
    )
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    engine_one, _ = scan(tmp_path)
    view_one = engine_one.commander_view("F100")
    assert view_one is not None
    snapshot_one = {
        mission.mission_id: (
            mission.state,
            mission.progress_kills,
            mission.reward,
        )
        for mission in view_one.missions
    }

    clipboard = ClipboardStore()
    clipboard.add_source(view_one.missions[0])
    assert clipboard.entries
    clipboard.clear()

    engine_two, _ = scan(tmp_path)
    view_two = engine_two.commander_view("F100")
    snapshot_two = {
        m.mission_id: (m.state, m.progress_kills, m.reward)
        for m in view_two.missions
    }
    assert snapshot_one == snapshot_two
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert before == after
    assert not clipboard.entries


def test_acceptance_19_summary_calculations():
    tracker = CommanderTracker(key="F", fid="F")
    a30 = add_mission(tracker, 1, issuer="A", kills=30, reward=1000, accepted_at=1)
    a20 = add_mission(tracker, 2, issuer="A", kills=20, reward=2000, accepted_at=2)
    b40 = add_mission(tracker, 3, issuer="B", kills=40, reward=3000, accepted_at=3)
    a30.progress_kills = 10
    a20.progress_kills = 5
    b40.progress_kills = 40
    b40.state = MissionState.REDIRECTED

    summary = build_target_summaries(tracker)[0]
    assert summary.max_stack_contract == 50
    assert summary.remaining_required == 35
    assert summary.total_contract == 90
    assert summary.overlap_ratio == 1.8
    assert summary.total_reward == 6000
    assert summary.turn_in_reward == 3000
    assert summary.kills_to_next_completion == 20


def test_acceptance_20_no_writes(tmp_path: Path):
    now = datetime.now(timezone.utc)
    path = journal_path(tmp_path, now, 1)
    write_journal(
        path,
        [fileheader(now), commander(timestamp=now), docked(timestamp=now), mission_accepted(1, timestamp=now)],
    )
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    async def scenario() -> None:
        app = TrackerApp(Config(journal_dir=tmp_path))
        async with app.run_test(size=(100, 30)) as pilot:
            await asyncio.to_thread(app._scan_finished_event.wait, 3)
            for _ in range(8):
                await pilot.pause()
            assert app.engine.commander_view("F100") is not None
            app.action_quit()
            await pilot.pause()

    asyncio.run(scenario())
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
    assert not any(p.name.startswith("mission") for p in tmp_path.iterdir())
