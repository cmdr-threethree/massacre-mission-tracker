from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest

from massacre_tracker.journal.discovery import (
    JournalDirectoryError,
    discover_journal_files,
    file_in_window,
    journal_filename_timestamp,
)
from tests.builders import BASE_TIME, journal_path


def set_mtime(path: Path, dt) -> None:
    seconds = dt.timestamp()
    os.utime(path, (seconds, seconds))


def test_filename_timestamp_parsing():
    assert (
        journal_filename_timestamp("Journal.2026-10-19T120000.01.log")
        == BASE_TIME
    )
    assert journal_filename_timestamp("Journal.2026-10-19T120000.log") == BASE_TIME
    assert journal_filename_timestamp("Journal.bad.log") is None
    assert journal_filename_timestamp("Status.json") is None


def test_discovery_ignores_non_journal_files(tmp_path: Path):
    journal_path(tmp_path).write_text("{}\n")
    (tmp_path / "Status.json").write_text("{}")
    (tmp_path / "Market.json").write_text("{}")
    result = discover_journal_files(tmp_path)
    assert len(result.files) == 1


def test_missing_directory_raises(tmp_path: Path):
    with pytest.raises(JournalDirectoryError):
        discover_journal_files(tmp_path / "missing")


def test_eight_day_window_uses_filename_or_mtime(tmp_path: Path):
    cutoff = BASE_TIME - timedelta(days=8)

    old_name_old_mtime = journal_path(tmp_path, BASE_TIME - timedelta(days=10), 1)
    old_name_old_mtime.write_text("{}\n")
    set_mtime(old_name_old_mtime, BASE_TIME - timedelta(days=10))

    old_name_recent_mtime = journal_path(tmp_path, BASE_TIME - timedelta(days=9), 2)
    old_name_recent_mtime.write_text("{}\n")
    set_mtime(old_name_recent_mtime, BASE_TIME)

    recent_name = journal_path(tmp_path, BASE_TIME - timedelta(days=1), 3)
    recent_name.write_text("{}\n")
    set_mtime(recent_name, BASE_TIME - timedelta(days=1))

    result = discover_journal_files(tmp_path)
    by_name = {item.path.name: item for item in result.files}
    assert not file_in_window(by_name[old_name_old_mtime.name], cutoff)
    assert file_in_window(by_name[old_name_recent_mtime.name], cutoff)
    assert file_in_window(by_name[recent_name.name], cutoff)


def test_invalid_filename_can_still_be_in_window_by_mtime(tmp_path: Path):
    path = tmp_path / "Journal.not-a-date.log"
    path.write_text("{}\n")
    set_mtime(path, BASE_TIME)
    result = discover_journal_files(tmp_path)
    assert len(result.files) == 1
    assert result.files[0].filename_timestamp is None
    assert file_in_window(result.files[0], BASE_TIME - timedelta(days=8))


def test_tailer_discovery_can_skip_filename_timestamp_parsing(tmp_path: Path):
    path = journal_path(tmp_path)
    path.write_text("{}\n")
    result = discover_journal_files(tmp_path, parse_filename_timestamps=False)
    assert len(result.files) == 1
    assert result.files[0].filename_timestamp is None
