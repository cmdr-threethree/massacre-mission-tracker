"""Live journal directory polling."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from ..config import Config
from ..domain.models import StationContext
from ..engine import SessionState, TrackerEngine, utcnow
from ..journal.discovery import (
    DiscoveredFile,
    JournalDirectoryError,
    discover_journal_files,
)
from ..journal.events import (
    EventEnvelope,
    GAMEPLAY_EVENTS,
    game_major_version,
)
from ..journal.parser import line_hash, parse_journal_line
from ..journal.reader import read_appended, split_complete_lines
from ..journal.scanner import _station_after_event


@dataclass(frozen=True, slots=True)
class TailPollResult:
    changed: bool
    new_files: int
    truncated_files: int
    lines_read: int
    events_applied: int


class JournalTailer:
    def __init__(
        self,
        engine: TrackerEngine,
        config: Config,
        *,
        clock: Callable[[], datetime] = utcnow,
        fallback_seconds: float = 10.0,
    ) -> None:
        self.engine = engine
        self.config = config
        self.clock = clock
        self.fallback_seconds = fallback_seconds
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()

    def poll_once(self, now: datetime | None = None) -> TailPollResult:
        current = now or self.clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        try:
            discovery = discover_journal_files(
                self.config.journal_dir,
                parse_filename_timestamps=False,
            )
        except JournalDirectoryError as exc:
            self.engine.add_warning(str(exc))
            self.engine.set_last_poll(current)
            return TailPollResult(False, 0, 0, 0, 0)

        for error in discovery.errors:
            self.engine.add_warning(error)

        discovered_paths = {item.path for item in discovery.files}
        new_files = 0
        truncated_files = 0
        session_flags_changed = False
        lines_read = 0
        envelopes: list[EventEnvelope] = []

        for item in discovery.files:
            session = self.engine.session_for(item.path)
            if session is None:
                session = self.engine.register_session(
                    item.path,
                    offset=0,
                    first_seen=current,
                    mtime=item.mtime,
                )
                new_files += 1
            else:
                session.mtime = item.mtime
                if session.missing:
                    self.engine.set_session_missing(session, False)
                    session_flags_changed = True

            file_envelopes, file_lines, truncated = self._poll_file(
                item, session, current
            )
            envelopes.extend(file_envelopes)
            lines_read += file_lines
            if truncated:
                truncated_files += 1

        with self.engine.lock:
            for path, session in self.engine.sessions.items():
                if path not in discovered_paths and not session.missing:
                    self.engine.set_session_missing(session, True)
                    session_flags_changed = True
                    self.engine.add_warning(
                        f"{session.filename}: file is missing; "
                        "waiting for it to return"
                    )

        applied = self.engine.apply_batch(envelopes)
        resolved = self.engine.resolve_unknown_sessions(
            now=current,
            deadline_seconds=self.fallback_seconds,
        )
        self.engine.set_last_poll(current)

        changed = bool(
            envelopes
            or new_files
            or truncated_files
            or session_flags_changed
            or resolved
        )
        return TailPollResult(
            changed=changed,
            new_files=new_files,
            truncated_files=truncated_files,
            lines_read=lines_read,
            events_applied=applied,
        )

    def _poll_file(
        self,
        discovered: DiscoveredFile,
        session: SessionState,
        now: datetime,
    ) -> tuple[list[EventEnvelope], int, bool]:
        try:
            data, truncated = read_appended(
                discovered.path,
                session.offset,
                discovered.size,
            )
        except OSError as exc:
            if not session.missing:
                self.engine.add_warning(
                    f"Cannot read {session.filename}: {exc}"
                )
            self.engine.set_session_missing(session, True)
            return [], 0, False

        if truncated:
            self._reset_after_truncation(session)
            self.engine.add_warning(
                f"{session.filename}: file was truncated; reprocessing from start"
            )

        if not data:
            return [], 0, truncated

        buffer = session.partial + data
        lines, partial, next_line_number = split_complete_lines(
            buffer, session.line_number
        )
        session.partial = partial
        session.line_number = next_line_number
        session.offset = len(data) if truncated else session.offset + len(data)

        local_live = session.live
        local_station = session.station
        envelopes: list[EventEnvelope] = []

        for line_number, raw in lines:
            parsed = parse_journal_line(raw)
            if parsed.malformed:
                self.engine.note_malformed()
                continue
            if not parsed.relevant:
                continue
            assert parsed.event is not None
            assert parsed.data is not None
            assert parsed.timestamp is not None

            event = parsed.event
            data_dict = parsed.data
            if event == "Fileheader":
                major = game_major_version(data_dict.get("gameversion"))
                local_live = True if major is None else major >= 4
            elif event in {"Location", "Docked", "Undocked"}:
                local_station = _station_after_event(
                    event, data_dict, local_station
                )

            if local_live is False and event in GAMEPLAY_EVENTS:
                continue

            envelopes.append(
                EventEnvelope(
                    event=event,
                    timestamp=parsed.timestamp,
                    data=data_dict,
                    path=discovered.path,
                    filename=discovered.path.name,
                    line_number=line_number,
                    live=local_live,
                    station=local_station or StationContext(),
                    line_hash=line_hash(raw),
                )
            )

        return envelopes, len(lines), truncated

    def _reset_after_truncation(self, session: SessionState) -> None:
        session.offset = 0
        session.partial = b""
        session.line_number = 0
        session.live = None
        session.station = None
        session.pending.clear()
        # applied_hashes and commander identity intentionally survive.

    def run_forever(
        self,
        on_cycle: Callable[[TailPollResult], None] | None = None,
    ) -> None:
        """Poll until ``stop_event`` is set."""

        while not self.stop_event.is_set():
            result = self.poll_once()
            if on_cycle is not None:
                on_cycle(result)
            self.wake_event.wait(self.config.poll_interval)
            self.wake_event.clear()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()

    def poll_now(self) -> None:
        self.wake_event.set()
