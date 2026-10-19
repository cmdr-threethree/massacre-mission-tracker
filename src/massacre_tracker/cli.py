"""Command-line interface."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from .config import Config, ConfigError, DEFAULT_DAYS, DEFAULT_POLL_INTERVAL

ENV_JOURNAL_DIR = "ED_JOURNAL_DIR"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="massacre-tracker",
        description=(
            "Track Elite Dangerous massacre missions live from journal files. "
            "All state is reconstructed in memory and is never written to disk."
        ),
    )
    parser.add_argument(
        "--journal-dir",
        metavar="PATH",
        help=(
            "Directory containing Journal.*.log files. "
            f"May also be set with {ENV_JOURNAL_DIR}."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        metavar="N",
        help=f"Days of journals to reconstruct at startup (default: {DEFAULT_DAYS}).",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        metavar="SECONDS",
        help=(
            "Journal polling interval in seconds "
            f"(default: {DEFAULT_POLL_INTERVAL})."
        ),
    )
    parser.add_argument(
        "--commander",
        metavar="FID_OR_NAME",
        help="Initially select a commander by FID or name.",
    )
    parser.add_argument(
        "--no-color",
        action="store_false",
        dest="color",
        help="Disable colour in the terminal UI.",
    )
    return parser


def build_config(
    args: argparse.Namespace,
    environ: Mapping[str, str] | None = None,
) -> Config:
    env = os.environ if environ is None else environ
    raw_dir = args.journal_dir or env.get(ENV_JOURNAL_DIR)
    if not raw_dir:
        raise ConfigError(
            "--journal-dir is required (or set the "
            f"{ENV_JOURNAL_DIR} environment variable)"
        )

    commander = (args.commander or "").strip() or None
    return Config(
        journal_dir=Path(raw_dir).expanduser(),
        days=args.days,
        poll_interval=args.poll,
        commander_filter=commander,
        color=args.color,
    )


def parse_config(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return build_config(args, environ)
    except ConfigError as exc:
        parser.error(str(exc))
        raise  # pragma: no cover - argparse.error exits


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_config(argv)
    # Import lazily so --help and configuration errors do not require Textual.
    from .ui.app import TrackerApp

    TrackerApp(config).run()
    return 0
