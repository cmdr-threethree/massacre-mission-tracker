"""Journal file discovery and the startup time window."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_FILENAME_RE = re.compile(
    r"^Journal\.(\d{4}-\d{2}-\d{2})T(\d{6})(?:\.\d+)?\.log$"
)


class JournalDirectoryError(RuntimeError):
    """The configured journal directory cannot be used."""


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    path: Path
    size: int
    mtime: datetime
    filename_timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    files: tuple[DiscoveredFile, ...]
    errors: tuple[str, ...] = ()


def journal_filename_timestamp(filename: str) -> datetime | None:
    match = _FILENAME_RE.match(filename)
    if match is None:
        return None
    text = f"{match.group(1)}T{match.group(2)}"
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def discover_journal_files(directory: Path) -> DiscoveryResult:
    """Discover direct children matching ``Journal.*.log``."""

    if not directory.exists():
        raise JournalDirectoryError(f"Journal directory does not exist: {directory}")
    if not directory.is_dir():
        raise JournalDirectoryError(f"Journal path is not a directory: {directory}")

    files: list[DiscoveredFile] = []
    errors: list[str] = []
    try:
        candidates = sorted(directory.glob("Journal.*.log"))
    except OSError as exc:
        raise JournalDirectoryError(
            f"Cannot list journal directory {directory}: {exc}"
        ) from exc

    for path in candidates:
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError as exc:
            errors.append(f"Cannot stat {path.name}: {exc}")
            continue
        files.append(
            DiscoveredFile(
                path=path,
                size=stat.st_size,
                mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                filename_timestamp=journal_filename_timestamp(path.name),
            )
        )

    return DiscoveryResult(files=tuple(files), errors=tuple(errors))


def file_in_window(discovered: DiscoveredFile, cutoff: datetime) -> bool:
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    if (
        discovered.filename_timestamp is not None
        and discovered.filename_timestamp >= cutoff
    ):
        return True
    return discovered.mtime >= cutoff
