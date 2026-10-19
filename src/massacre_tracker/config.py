"""Immutable runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path

DEFAULT_DAYS = 8
DEFAULT_POLL_INTERVAL = 1.0


class ConfigError(ValueError):
    """Raised for invalid command-line configuration."""


@dataclass(frozen=True, slots=True)
class Config:
    """Read-only runtime configuration.

    The application never persists this configuration or any game state.
    """

    journal_dir: Path
    days: int = DEFAULT_DAYS
    poll_interval: float = DEFAULT_POLL_INTERVAL
    commander_filter: str | None = None
    color: bool = True

    def __post_init__(self) -> None:
        if self.days <= 0:
            raise ConfigError("--days must be greater than zero")
        if not isfinite(self.poll_interval) or self.poll_interval <= 0:
            raise ConfigError("--poll must be a finite number greater than zero")

    def cutoff(self, now: datetime | None = None) -> datetime:
        """Return the reconstruction cutoff as an aware UTC datetime."""

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc) - timedelta(days=self.days)
