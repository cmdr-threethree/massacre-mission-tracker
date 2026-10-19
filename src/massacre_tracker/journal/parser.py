"""Fast journal line parsing.

Journal files contain far more irrelevant events than relevant ones. A cheap
byte-level event-name check avoids constructing a JSON object for every line.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .events import RELEVANT_EVENTS, parse_timestamp

_EVENT_RE = re.compile(rb'"event"\s*:\s*"([^"\\]+)"')


@dataclass(frozen=True, slots=True)
class LineParseResult:
    event: str | None = None
    data: dict[str, Any] | None = None
    timestamp: datetime | None = None
    malformed: bool = False

    @property
    def relevant(self) -> bool:
        return self.event is not None and self.data is not None


def line_hash(raw: bytes) -> str:
    return hashlib.sha1(raw).hexdigest()


def parse_journal_line(raw: bytes) -> LineParseResult:
    """Parse one complete line, skipping irrelevant events cheaply."""

    if not raw.strip():
        return LineParseResult()

    match = _EVENT_RE.search(raw)
    if match is None:
        return LineParseResult(malformed=True)

    try:
        event_name = match.group(1).decode("utf-8")
    except UnicodeDecodeError:
        return LineParseResult(malformed=True)

    if event_name not in RELEVANT_EVENTS:
        return LineParseResult()

    try:
        decoded = raw.decode("utf-8")
        data = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return LineParseResult(event=event_name, malformed=True)

    if not isinstance(data, dict):
        return LineParseResult(event=event_name, malformed=True)

    actual_event = data.get("event")
    if not isinstance(actual_event, str):
        return LineParseResult(malformed=True)
    if actual_event not in RELEVANT_EVENTS:
        return LineParseResult()

    timestamp = parse_timestamp(data.get("timestamp"))
    if timestamp is None:
        return LineParseResult(event=actual_event, malformed=True)

    return LineParseResult(
        event=actual_event,
        data=data,
        timestamp=timestamp,
    )
