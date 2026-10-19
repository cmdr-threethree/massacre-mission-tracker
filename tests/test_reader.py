from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from massacre_tracker.journal.reader import (
    iter_file_chunks,
    read_appended,
    split_complete_lines,
)


def test_split_complete_lines_carries_partial():
    lines, partial, next_line = split_complete_lines(b"one\ntwo\nthree", 0)
    assert lines == [(0, b"one"), (1, b"two")]
    assert partial == b"three"
    assert next_line == 2


def test_split_handles_crlf_and_blank_lines():
    lines, partial, next_line = split_complete_lines(b"one\r\n\r\nthree\r\n", 5)
    assert lines == [(5, b"one"), (6, b""), (7, b"three")]
    assert partial == b""
    assert next_line == 8


def test_iter_file_chunks_reports_exact_end_and_partial(tmp_path: Path):
    path = tmp_path / "journal.log"
    path.write_bytes(b"a\nb\nc")
    chunks = list(iter_file_chunks(path, 0, path.stat().st_size, chunk_size=2))
    lines = [line for chunk in chunks for line in chunk.lines]
    assert lines == [(0, b"a"), (1, b"b")]
    assert chunks[-1].partial == b"c"
    assert chunks[-1].end_offset == path.stat().st_size


def test_iter_file_chunks_does_not_read_past_observed_size(tmp_path: Path):
    path = tmp_path / "journal.log"
    path.write_bytes(b"a\nb\n")
    chunks = list(iter_file_chunks(path, 0, 2))
    assert [line for chunk in chunks for line in chunk.lines] == [(0, b"a")]


def test_iter_file_chunks_reads_multibyte_content(tmp_path: Path):
    path = tmp_path / "journal.log"
    path.write_bytes("é\nü\n".encode("utf-8"))
    chunks = list(iter_file_chunks(path, 0, path.stat().st_size, chunk_size=1))
    lines = [line for chunk in chunks for line in chunk.lines]
    assert [raw.decode("utf-8") for _, raw in lines] == ["é", "ü"]


def test_read_appended_and_truncation(tmp_path: Path):
    path = tmp_path / "journal.log"
    path.write_bytes(b"12345")
    data, truncated = read_appended(path, 0)
    assert data == b"12345"
    assert not truncated

    data, truncated = read_appended(path, 5)
    assert data == b""
    assert not truncated

    path.write_bytes(b"abc")
    data, truncated = read_appended(path, 5)
    assert data == b"abc"
    assert truncated


def test_read_appended_accepts_size_without_extra_stat(tmp_path: Path):
    path = tmp_path / "journal.log"
    path.write_bytes(b"abcdef")
    with patch.object(
        Path,
        "stat",
        side_effect=AssertionError("unexpected stat"),
    ):
        data, truncated = read_appended(path, 2, size=6)
    assert data == b"cdef"
    assert not truncated
