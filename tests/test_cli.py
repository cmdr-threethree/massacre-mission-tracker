from __future__ import annotations

from pathlib import Path

import pytest

from massacre_tracker.cli import build_parser, parse_config
from massacre_tracker.config import Config, ConfigError, DEFAULT_DAYS


def test_defaults_and_commander_filter():
    config = parse_config(
        ["--journal-dir", "/tmp/journals", "--commander", "Jameson"],
        environ={},
    )
    assert config.journal_dir == Path("/tmp/journals")
    assert config.days == DEFAULT_DAYS
    assert config.poll_interval == 2.0
    assert config.commander_filter == "Jameson"
    assert config.color is True


def test_environment_journal_directory():
    config = parse_config([], environ={"ED_JOURNAL_DIR": "/tmp/env-journals"})
    assert config.journal_dir == Path("/tmp/env-journals")


def test_cli_overrides_environment():
    config = parse_config(
        ["--journal-dir", "/tmp/cli"],
        environ={"ED_JOURNAL_DIR": "/tmp/env"},
    )
    assert config.journal_dir == Path("/tmp/cli")


def test_no_color_and_empty_commander():
    config = parse_config(
        ["--journal-dir", "/tmp/j", "--no-color", "--commander", "   "],
        environ={},
    )
    assert config.color is False
    assert config.commander_filter is None


def test_missing_directory_argument_exits():
    with pytest.raises(SystemExit):
        parse_config([], environ={})


@pytest.mark.parametrize(
    "argv",
    [
        ["--journal-dir", "/tmp/j", "--days", "0"],
        ["--journal-dir", "/tmp/j", "--poll", "0"],
        ["--journal-dir", "/tmp/j", "--poll", "-1"],
        ["--journal-dir", "/tmp/j", "--poll", "nan"],
        ["--journal-dir", "/tmp/j", "--poll", "inf"],
    ],
)
def test_invalid_numeric_values_exit(argv):
    with pytest.raises(SystemExit):
        parse_config(argv, environ={})


def test_config_rejects_invalid_values():
    with pytest.raises(ConfigError):
        Config(journal_dir=Path("/tmp"), days=0)
    with pytest.raises(ConfigError):
        Config(journal_dir=Path("/tmp"), poll_interval=0)


def test_cutoff_uses_injected_clock():
    from datetime import datetime, timezone

    config = Config(journal_dir=Path("/tmp"), days=8)
    now = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    cutoff = config.cutoff(now)
    assert cutoff == datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def test_parser_help_lists_required_options():
    help_text = build_parser().format_help()
    for option in ("--journal-dir", "--days", "--poll", "--commander", "--no-color"):
        assert option in help_text
