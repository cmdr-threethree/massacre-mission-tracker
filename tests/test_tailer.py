from __future__ import annotations

import os
from pathlib import Path

from massacre_tracker.config import Config
from massacre_tracker.domain.models import MissionState
from massacre_tracker.engine import TrackerEngine
from massacre_tracker.journal.scanner import JournalScanner
from massacre_tracker.journal.tailer import JournalTailer
from tests.builders import (
    BASE_TIME,
    at,
    bounty,
    commander,
    docked,
    fileheader,
    journal_path,
    mission_accepted,
    write_journal,
)


def set_mtime(path: Path, dt) -> None:
    seconds = dt.timestamp()
    os.utime(path, (seconds, seconds))


def make_engine(tmp_path: Path) -> tuple[TrackerEngine, Config]:
    engine = TrackerEngine()
    config = Config(journal_dir=tmp_path)
    scanner = JournalScanner(engine, config, clock=lambda: BASE_TIME)
    scanner.scan()
    return engine, config


def initial_file(tmp_path: Path, *, fid: str = "F100") -> Path:
    path = journal_path(tmp_path, BASE_TIME, 1)
    write_journal(
        path,
        [fileheader(), commander(fid=fid), docked()],
    )
    set_mtime(path, BASE_TIME)
    return path


def test_new_file_after_startup_is_read_from_zero(tmp_path: Path):
    engine, config = make_engine(tmp_path)
    tailer = JournalTailer(engine, config, clock=lambda: at(1))

    path = journal_path(tmp_path, at(1), 1)
    write_journal(
        path,
        [
            fileheader(),
            commander(),
            docked(),
            mission_accepted(1),
        ],
    )
    result = tailer.poll_once(now=at(1))
    assert result.new_files == 1
    assert result.events_applied == 4

    view = engine.commander_view("F100")
    assert view is not None
    assert [m.mission_id for m in view.missions] == [1]


def test_append_to_existing_file_is_processed_once(tmp_path: Path):
    path = initial_file(tmp_path)
    engine, config = make_engine(tmp_path)
    tailer = JournalTailer(engine, config, clock=lambda: at(1))

    with path.open("a", encoding="utf-8") as handle:
        handle.write(mission_accepted(1) + "\n")
    result = tailer.poll_once(now=at(1))
    assert result.events_applied == 1
    assert [m.mission_id for m in engine.commander_view("F100").missions] == [1]

    result = tailer.poll_once(now=at(2))
    assert result.events_applied == 0
    assert engine.stats().events_processed == 4


def test_partial_line_is_carried_across_polls(tmp_path: Path):
    path = initial_file(tmp_path)
    engine, config = make_engine(tmp_path)
    tailer = JournalTailer(engine, config, clock=lambda: at(1))

    line = mission_accepted(1) + "\n"
    split = len(line) // 2
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line[:split])
    result = tailer.poll_once(now=at(1))
    assert result.lines_read == 0
    assert engine.commander_view("F100").missions == ()

    with path.open("a", encoding="utf-8") as handle:
        handle.write(line[split:])
    result = tailer.poll_once(now=at(2))
    assert result.lines_read == 1
    assert [m.mission_id for m in engine.commander_view("F100").missions] == [1]

    tailer.poll_once(now=at(3))
    assert len(engine.commander_view("F100").missions) == 1


def test_truncation_reprocesses_without_double_crediting(tmp_path: Path):
    path = initial_file(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(mission_accepted(1, kills=2) + "\n")
    set_mtime(path, BASE_TIME)

    engine, config = make_engine(tmp_path)
    tailer = JournalTailer(engine, config, clock=lambda: at(1))
    mission = engine.commander_view("F100").missions[0]
    assert mission.progress_kills == 0

    replacement = "\n".join(
        [
            fileheader(),
            commander(),
            docked(),
            bounty(timestamp=at(2)),
        ]
    ) + "\n"
    assert len(replacement.encode()) < path.stat().st_size
    path.write_text(replacement, encoding="utf-8")

    result = tailer.poll_once(now=at(2))
    assert result.truncated_files == 1
    view = engine.commander_view("F100")
    assert view is not None
    mission = view.missions[0]
    assert mission.progress_kills == 1
    assert mission.state is MissionState.ACTIVE

    # Rewriting identical content must not credit the bounty again.
    path.write_text(replacement, encoding="utf-8")
    tailer.poll_once(now=at(3))
    assert engine.commander_view("F100").missions[0].progress_kills == 1


def test_malformed_line_does_not_stop_processing(tmp_path: Path):
    path = initial_file(tmp_path)
    engine, config = make_engine(tmp_path)
    tailer = JournalTailer(engine, config, clock=lambda: at(1))

    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"event":"Bounty", broken\n')
    tailer.poll_once(now=at(1))
    assert engine.stats().malformed_lines == 1

    with path.open("a", encoding="utf-8") as handle:
        handle.write(mission_accepted(1) + "\n")
    tailer.poll_once(now=at(2))
    assert [m.mission_id for m in engine.commander_view("F100").missions] == [1]


def test_bounty_through_tailer_updates_progress_and_redirects(tmp_path: Path):
    path = initial_file(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(mission_accepted(1, kills=1) + "\n")
    engine, config = make_engine(tmp_path)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(bounty() + "\n")
    tailer = JournalTailer(engine, config, clock=lambda: at(1))
    tailer.poll_once(now=at(1))

    mission = engine.commander_view("F100").missions[0]
    assert mission.progress_kills == 1
    assert mission.state is MissionState.REDIRECTED


def test_new_file_without_identity_uses_timeout_fallback(tmp_path: Path):
    engine, config = make_engine(tmp_path)
    tailer = JournalTailer(engine, config, clock=lambda: at(1))

    path = journal_path(tmp_path, at(1), 5)
    write_journal(path, [docked(), mission_accepted(1)])
    set_mtime(path, at(1))

    tailer.poll_once(now=at(1))
    session = engine.session_for(path)
    assert session is not None
    assert session.live is None
    assert len(session.pending) == 1

    tailer.poll_once(now=at(12))
    keys = engine.commander_keys()
    assert len(keys) == 1
    view = engine.commander_view(keys[0])
    assert view is not None
    assert len(view.missions) == 1


def test_missing_file_does_not_crash_and_is_reported(tmp_path: Path):
    path = initial_file(tmp_path)
    engine, config = make_engine(tmp_path)
    tailer = JournalTailer(engine, config, clock=lambda: at(1))
    path.unlink()

    result = tailer.poll_once(now=at(1))
    assert not result.changed
    session = engine.session_for(path)
    assert session is not None
    assert session.missing
    assert engine.stats().unreadable_files == 1
