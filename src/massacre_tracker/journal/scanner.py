"""Startup reconstruction from the recent journal window."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from ..config import Config
from ..domain.models import StationContext
from ..domain.values import as_bool, as_int, as_text
from ..engine import TrackerEngine, utcnow
from ..journal.discovery import (
    DiscoveredFile,
    discover_journal_files,
    file_in_window,
)
from ..journal.events import (
    EventEnvelope,
    GAMEPLAY_EVENTS,
    game_major_version,
)
from ..journal.parser import line_hash, parse_journal_line
from ..journal.reader import iter_file_chunks


@dataclass(frozen=True, slots=True)
class ScanProgress:
    files_done: int
    files_total: int
    current_file: str
    lines_read: int


@dataclass(frozen=True, slots=True)
class ScanReport:
    files_total: int
    files_included: int
    files_excluded: int
    events_applied: int
    lines_read: int
    cutoff: datetime


ProgressCallback = Callable[[ScanProgress], None]


class JournalScanner:
    def __init__(
        self,
        engine: TrackerEngine,
        config: Config,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.engine = engine
        self.config = config
        self.clock = clock

    def scan(self, progress: ProgressCallback | None = None) -> ScanReport:
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cutoff = self.config.cutoff(now)

        discovery = discover_journal_files(self.config.journal_dir)
        for error in discovery.errors:
            self.engine.add_warning(error)

        files = discovery.files
        total = len(files)
        envelopes: list[EventEnvelope] = []
        included = 0
        excluded = 0
        lines_read = 0

        for index, discovered in enumerate(files, start=1):
            session = self.engine.register_session(
                discovered.path,
                offset=discovered.size,
                first_seen=now,
                mtime=discovered.mtime,
                reset=True,
            )
            if file_in_window(discovered, cutoff):
                included += 1
                file_envelopes, file_lines = self._scan_file(
                    discovered,
                    cutoff,
                    on_chunk=(
                        None
                        if progress is None
                        else lambda file_lines, index=index, name=discovered.path.name: progress(
                            ScanProgress(
                                files_done=index - 1,
                                files_total=total,
                                current_file=name,
                                lines_read=lines_read + file_lines,
                            )
                        )
                    ),
                )
                envelopes.extend(file_envelopes)
                lines_read += file_lines
            else:
                excluded += 1
                session.offset = discovered.size
                session.partial = b""
                session.line_number = 0

            if progress is not None:
                progress(
                    ScanProgress(
                        files_done=index,
                        files_total=total,
                        current_file=discovered.path.name,
                        lines_read=lines_read,
                    )
                )

        applied = self.engine.apply_batch(envelopes)
        self.engine.resolve_unknown_sessions(now=now, deadline_seconds=None)
        self.engine.set_last_poll(now)

        return ScanReport(
            files_total=total,
            files_included=included,
            files_excluded=excluded,
            events_applied=applied,
            lines_read=lines_read,
            cutoff=cutoff,
        )

    def _scan_file(
        self,
        discovered: DiscoveredFile,
        cutoff: datetime,
        on_chunk: Callable[[int], None] | None = None,
    ) -> tuple[list[EventEnvelope], int]:
        session = self.engine.session_for(discovered.path)
        assert session is not None
        session.offset = 0
        session.partial = b""
        session.line_number = 0
        session.live = None
        session.station = None
        session.parsed = True

        local_live: bool | None = None
        local_station: StationContext | None = None
        envelopes: list[EventEnvelope] = []
        lines_read = 0
        end_offset = 0

        try:
            for chunk in iter_file_chunks(
                discovered.path,
                start_offset=0,
                end_size=discovered.size,
            ):
                end_offset = chunk.end_offset
                for line_number, raw in chunk.lines:
                    lines_read += 1
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
                    data = parsed.data

                    if event == "Fileheader":
                        major = game_major_version(data.get("gameversion"))
                        local_live = True if major is None else major >= 4
                    elif event in {"Location", "Docked", "Undocked"}:
                        local_station = _station_after_event(
                            event, data, local_station
                        )

                    # Liveness/identity/station state is needed even when the
                    # event predates the window. Tracking events are not.
                    if (
                        event in GAMEPLAY_EVENTS
                        and parsed.timestamp < cutoff
                    ):
                        continue

                    envelopes.append(
                        EventEnvelope(
                            event=event,
                            timestamp=parsed.timestamp,
                            data=data,
                            path=discovered.path,
                            filename=discovered.path.name,
                            line_number=line_number,
                            live=local_live,
                            station=local_station or StationContext(),
                            line_hash=line_hash(raw),
                        )
                    )
                session.partial = chunk.partial
                session.line_number = chunk.next_line_number
                if on_chunk is not None:
                    on_chunk(lines_read)
        except OSError as exc:
            self.engine.add_warning(
                f"Cannot read {discovered.path.name}: {exc}"
            )
            self.engine.set_session_missing(session, True)
            # Do not replay the whole file later if it becomes readable:
            # only data appended after this failed scan will be considered.
            session.offset = discovered.size
            session.partial = b""
            session.line_number = 0
            return [], lines_read

        session.offset = end_offset
        session.live = local_live
        session.station = local_station
        return envelopes, lines_read


def _station_after_event(
    event: str,
    data: dict[str, object],
    current: StationContext | None,
) -> StationContext | None:
    if event == "Undocked":
        return None
    if event == "Location" and not as_bool(data.get("Docked")):
        return current
    return StationContext(
        system=as_text(data.get("StarSystem")),
        system_address=(
            as_int(data.get("SystemAddress"), 0)
            if data.get("SystemAddress") is not None
            else None
        ),
        station=as_text(data.get("StationName")),
    )
