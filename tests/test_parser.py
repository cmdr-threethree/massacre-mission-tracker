from __future__ import annotations

import json

from massacre_tracker.journal.events import (
    game_major_version,
    is_live_version,
    parse_timestamp,
)
from massacre_tracker.journal.parser import line_hash, parse_journal_line
from tests.builders import BASE_TIME, bounty, event, fileheader


def test_relevant_event_is_parsed():
    result = parse_journal_line(bounty().encode())
    assert result.relevant
    assert result.event == "Bounty"
    assert result.data is not None
    assert result.timestamp == BASE_TIME
    assert not result.malformed


def test_irrelevant_event_is_skipped_without_being_malformed():
    raw = event("FSDJump", StarSystem="Sol", JumpDist=10).encode()
    result = parse_journal_line(raw)
    assert not result.relevant
    assert not result.malformed


def test_whitespace_line_is_ignored():
    assert not parse_journal_line(b"   ").malformed


def test_malformed_json_with_relevant_event_is_counted():
    result = parse_journal_line(b'{"event":"Bounty", broken')
    assert result.malformed
    assert result.event == "Bounty"


def test_malformed_line_without_event_is_counted():
    assert parse_journal_line(b"this is not json").malformed


def test_event_with_invalid_timestamp_is_malformed():
    raw = b'{"event":"Bounty","timestamp":"not-a-time","TotalReward":10}'
    result = parse_journal_line(raw)
    assert result.malformed


def test_event_value_from_json_is_authoritative():
    raw = json.dumps(
        {"event": "Docked", "timestamp": "2026-10-19T12:00:00Z"}
    ).encode()
    assert parse_journal_line(raw).event == "Docked"


def test_empty_event_field_is_malformed():
    raw = b'{"event":42,"timestamp":"2026-10-19T12:00:00Z"}'
    assert parse_journal_line(raw).malformed


def test_line_hash_is_stable_and_content_sensitive():
    assert line_hash(b"abc") == line_hash(b"abc")
    assert line_hash(b"abc") != line_hash(b"abd")


def test_timestamp_parsing_is_utc():
    parsed = parse_timestamp("2026-10-19T12:00:00.123456Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.isoformat() == "2026-10-19T12:00:00.123456+00:00"
    assert parse_timestamp(None) is None
    assert parse_timestamp("") is None
    assert parse_timestamp("nope") is None
    assert parse_timestamp("2026-10-19T12:00:00") is not None


def test_game_version_major():
    assert game_major_version("4.0.0.100") == 4
    assert game_major_version("3.8.0.0") == 3
    assert game_major_version(None) is None
    assert game_major_version("unknown") is None
    assert is_live_version("4.0.0.100")
    assert not is_live_version("3.8.0.0")
    assert not is_live_version("unknown")


def test_fileheader_parses():
    result = parse_journal_line(fileheader(gameversion="4.0.1.0").encode())
    assert result.relevant
    assert result.event == "Fileheader"
