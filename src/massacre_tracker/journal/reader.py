"""Byte-accurate incremental journal reading."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CHUNK_SIZE = 1 << 20


@dataclass(frozen=True, slots=True)
class LineChunk:
    lines: tuple[tuple[int, bytes], ...]
    partial: bytes
    next_line_number: int
    end_offset: int


def split_complete_lines(
    buffer: bytes,
    first_line_number: int,
) -> tuple[list[tuple[int, bytes]], bytes, int]:
    """Split complete newline-terminated lines from a byte buffer.

    The returned partial buffer never contains a newline. Line numbers are
    zero-based and count every complete line, including blank lines.
    """

    parts = buffer.split(b"\n")
    partial = parts.pop()
    lines = [
        (first_line_number + index, part[:-1] if part.endswith(b"\r") else part)
        for index, part in enumerate(parts)
    ]
    return lines, partial, first_line_number + len(parts)


def iter_file_chunks(
    path: Path,
    start_offset: int,
    end_size: int,
    initial_partial: bytes = b"",
    start_line_number: int = 0,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Iterator[LineChunk]:
    """Read from ``start_offset`` to ``end_size`` in chunks."""

    buffer = initial_partial
    line_number = start_line_number
    position = start_offset
    if end_size <= start_offset:
        return

    with path.open("rb") as handle:
        handle.seek(start_offset)
        while position < end_size:
            wanted = min(chunk_size, end_size - position)
            chunk = handle.read(wanted)
            if not chunk:
                break
            position += len(chunk)
            buffer += chunk
            lines, buffer, line_number = split_complete_lines(
                buffer, line_number
            )
            yield LineChunk(
                lines=tuple(lines),
                partial=buffer,
                next_line_number=line_number,
                end_offset=position,
            )


def read_appended(
    path: Path,
    offset: int,
    size: int | None = None,
) -> tuple[bytes, bool]:
    """Read bytes after ``offset``.

    Returns ``(data, truncated)``. When the file is smaller than the offset,
    the returned data starts at byte zero and ``truncated`` is True.

    ``size`` may be supplied from a recent directory scan to avoid an extra
    ``stat`` call per poll.
    """

    if size is None:
        size = path.stat().st_size
    if size < offset:
        with path.open("rb") as handle:
            return handle.read(), True
    if size == offset:
        return b"", False
    with path.open("rb") as handle:
        handle.seek(offset)
        return handle.read(), False
