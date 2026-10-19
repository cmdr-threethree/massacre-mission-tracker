from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from massacre_tracker.config import Config
from massacre_tracker.engine import TrackerEngine
from massacre_tracker.journal.scanner import JournalScanner
from tests.builders import (
    BASE_TIME,
    at,
    commander,
    docked,
    event,
    fileheader,
    journal_path,
    mission_accepted,
    write_journal,
)


def set_mtime(path: Path, dt) -> None:
    seconds = dt.timestamp()
    os.utime(path, (seconds, seconds))


def make_scanner(
    tmp_path: Path,
    *,
    clock=lambda: BASE_TIME,
    days: int = 8,
) -> tuple[JournalScanner, TrackerEngine]:
    engine = TrackerEngine()
    config = Config(journal_dir=tmp_path, days=days)
    return JournalScanner(engine, config, clock=clock), engine


def test_eight_day_window_reconstructs_recent_and_watches_both(tmp_path: Path):
    old = journal_path(tmp_path, BASE_TIME - timedelta(days=10), 1)
    write_journal(
        old,
        [
            fileheader(BASE_TIME - timedelta(days=10)),
            commander(),
            docked(),
            mission_accepted(1),
        ],
    )
    set_mtime(old, BASE_TIME - timedelta(days=10))

    recent = journal_path(tmp_path, BASE_TIME - timedelta(days=1), 2)
    write_journal(
        recent,
        [
            fileheader(BASE_TIME - timedelta(days=1)),
            commander(),
            docked(),
            mission_accepted(2),
        ],
    )
    set_mtime(recent, BASE_TIME - timedelta(days=1))

    scanner, engine = make_scanner(tmp_path)
    report = scanner.scan()

    assert report.files_total == 2
    assert report.files_included == 1
    assert report.files_excluded == 1
    assert engine.stats().watched_files == 2
    old_session = engine.session_for(old)
    assert old_session is not None
    assert old_session.offset == old.stat().st_size
    assert old_session.line_number == 0

    view = engine.commander_view("F100")
    assert view is not None
    assert [m.mission_id for m in view.missions] == [2]


def test_long_running_session_uses_mtime_and_ignores_old_events(tmp_path: Path):
    path = journal_path(tmp_path, BASE_TIME - timedelta(days=9), 1)
    old_header = BASE_TIME - timedelta(days=9)
    write_journal(
        path,
        [
            fileheader(old_header),
            commander(timestamp=old_header),
            docked(timestamp=old_header),
            mission_accepted(1, timestamp=old_header),
            mission_accepted(2, timestamp=BASE_TIME - timedelta(days=1)),
        ],
    )
    set_mtime(path, BASE_TIME)

    scanner, engine = make_scanner(tmp_path)
    report = scanner.scan()
    assert report.files_included == 1

    view = engine.commander_view("F100")
    assert view is not None
    assert [m.mission_id for m in view.missions] == [2]
    assert view.missions[0].source_station == "Galileo"


def test_global_ordering_controls_kill_attribution(tmp_path: Path):
    path_a = journal_path(tmp_path, BASE_TIME, 1)
    path_b = journal_path(tmp_path, BASE_TIME, 2)

    write_journal(
        path_a,
        [
            fileheader(),
            commander(),
            docked(),
            mission_accepted(1, timestamp=at(10)),
        ],
    )
    write_journal(
        path_b,
        [
            fileheader(),
            commander(),
            docked(),
            mission_accepted(2, timestamp=at(5)),
            event(
                "Bounty",
                at(20),
                Target="sidewinder",
                VictimFaction="Target Faction",
                TotalReward=100,
            ),
        ],
    )
    set_mtime(path_a, BASE_TIME)
    set_mtime(path_b, BASE_TIME)

    scanner, engine = make_scanner(tmp_path)
    scanner.scan()
    view = engine.commander_view("F100")
    assert view is not None
    by_id = {m.mission_id: m for m in view.missions}
    assert by_id[2].progress_kills == 1
    assert by_id[1].progress_kills == 0


def test_legacy_session_produces_no_missions(tmp_path: Path):
    path = journal_path(tmp_path, BASE_TIME, 1)
    write_journal(
        path,
        [
            fileheader(gameversion="3.8.0.0"),
            commander(),
            docked(),
            mission_accepted(1),
        ],
    )
    set_mtime(path, BASE_TIME)
    scanner, engine = make_scanner(tmp_path)
    scanner.scan()
    view = engine.commander_view("F100")
    assert view is not None
    assert view.missions == ()


def test_missing_fileheader_and_commander_use_fallbacks(tmp_path: Path):
    path = journal_path(tmp_path, BASE_TIME, 1)
    write_journal(
        path,
        [
            docked(),
            mission_accepted(1),
        ],
    )
    set_mtime(path, BASE_TIME)
    scanner, engine = make_scanner(tmp_path)
    scanner.scan()

    keys = engine.commander_keys()
    assert len(keys) == 1
    assert keys[0].endswith(path.name)
    view = engine.commander_view(keys[0])
    assert view is not None
    assert len(view.missions) == 1
    stats = engine.stats()
    assert any("no Fileheader" in warning for warning in stats.warnings)
    assert any("no Commander" in warning for warning in stats.warnings)


def test_no_journal_files_reports_empty(tmp_path: Path):
    scanner, engine = make_scanner(tmp_path)
    report = scanner.scan()
    assert report.files_total == 0
    assert engine.stats().watched_files == 0


def test_progress_callback_is_called_per_file(tmp_path: Path):
    for index in range(1, 4):
        path = journal_path(tmp_path, BASE_TIME, index)
        write_journal(path, [fileheader(), commander(), docked()])
        set_mtime(path, BASE_TIME)
    scanner, _ = make_scanner(tmp_path)
    progress = []
    scanner.scan(progress=progress.append)
    assert progress
    assert progress[-1].files_done == 3
    assert progress[-1].files_total == 3
    assert any(item.files_done == 0 for item in progress)
