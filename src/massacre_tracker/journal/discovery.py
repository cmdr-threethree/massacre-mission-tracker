"""Journal file discovery and the startup time window."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from stat import S_ISREG

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


def discover_journal_files(
    directory: Path,
    *,
    parse_filename_timestamps: bool = True,
) -> DiscoveryResult:
    """Discover direct children matching ``Journal.*.log``.

    Use one directory scan and one stat per matching file. This runs once per
    poll, so avoiding redundant ``Path.exists``/``is_file``/``stat`` calls is
    important on large or remote journal directories.
    """

    files: list[DiscoveredFile] = []
    errors: list[str] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                name = entry.name
                if not name.startswith("Journal.") or not name.endswith(".log"):
                    continue
                try:
                    stat_result = entry.stat(follow_symlinks=True)
                except OSError as exc:
                    errors.append(f"Cannot stat {name}: {exc}")
                    continue
                if not S_ISREG(stat_result.st_mode):
                    continue
                files.append(
                    DiscoveredFile(
                        path=Path(entry.path),
                        size=stat_result.st_size,
                        mtime=datetime.fromtimestamp(
                            stat_result.st_mtime, tz=timezone.utc
                        ),
                        filename_timestamp=(
                            journal_filename_timestamp(name)
                            if parse_filename_timestamps
                            else None
                        ),
                    )
                )
    except FileNotFoundError as exc:
        raise JournalDirectoryError(
            f"Journal directory does not exist: {directory}"
        ) from exc
    except NotADirectoryError as exc:
        raise JournalDirectoryError(
            f"Journal path is not a directory: {directory}"
        ) from exc
    except OSError as exc:
        raise JournalDirectoryError(
            f"Cannot list journal directory {directory}: {exc}"
        ) from exc

    files.sort(key=lambda item: item.path.name)
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
