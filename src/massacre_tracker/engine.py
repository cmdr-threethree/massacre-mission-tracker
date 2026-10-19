"""Central event routing and in-memory application state."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from .domain.kills import adjust_all_stacks, adjust_mission, apply_bounty
from .domain.lifecycle import (
    apply_mission_abandoned,
    apply_mission_accepted,
    apply_mission_completed,
    apply_mission_failed,
    apply_mission_redirected,
    reconcile_missions,
    set_manual_state,
)
from .domain.models import (
    Bounty,
    CommanderTracker,
    Mission,
    MissionState,
    StationContext,
)
from .domain.summaries import (
    StackSummary,
    TargetSummary,
    build_stack_summaries,
    build_target_summaries,
)
from .domain.values import as_bool, as_int, as_text
from .journal.events import (
    EventEnvelope,
    GAMEPLAY_EVENTS,
    game_major_version,
)

UNKNOWN_KEY_PREFIX = "__unknown__:"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class SessionState:
    path: Path
    first_seen: datetime
    offset: int = 0
    partial: bytes = b""
    line_number: int = 0
    commander_fid: str | None = None
    commander_name: str | None = None
    station: StationContext | None = None
    live: bool | None = None
    last_event_at: datetime | None = None
    pending: list[EventEnvelope] = field(default_factory=list)
    applied_hashes: set[str] = field(default_factory=set)
    unknown_key: str | None = None
    missing: bool = False
    parsed: bool = False
    unknown_warned: bool = False
    missing_header_warned: bool = False

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def tracker_key(self) -> str | None:
        if self.commander_fid:
            return self.commander_fid
        return self.unknown_key


@dataclass(frozen=True, slots=True)
class SessionInfo:
    path: Path
    filename: str
    commander_fid: str | None
    commander_name: str | None
    system: str | None
    station: str | None
    live: bool | None
    last_event_at: datetime | None
    missing: bool
    offset: int
    pending_count: int


@dataclass(frozen=True, slots=True)
class CommanderView:
    key: str
    fid: str | None
    name: str | None
    display_name: str
    missions: tuple[Mission, ...]
    bounties: tuple[Bounty, ...]
    sessions: tuple[SessionInfo, ...]
    station_names: frozenset[str]

    def stack_summaries(self) -> list[StackSummary]:
        synthetic = CommanderTracker(
            key=self.key,
            fid=self.fid,
            name=self.name,
            missions={m.mission_id: m for m in self.missions},
        )
        return build_stack_summaries(synthetic)

    def target_summaries(self) -> list[TargetSummary]:
        synthetic = CommanderTracker(
            key=self.key,
            fid=self.fid,
            name=self.name,
            missions={m.mission_id: m for m in self.missions},
        )
        return build_target_summaries(synthetic)


@dataclass(frozen=True, slots=True)
class EngineStats:
    watched_files: int
    active_sessions: int
    commanders: int
    events_processed: int
    malformed_lines: int
    unreadable_files: int
    last_poll: datetime | None
    warnings: tuple[str, ...]


class TrackerEngine:
    """Owns all mutable state. Public methods are lock-protected."""

    def __init__(self) -> None:
        self.sessions: dict[Path, SessionState] = {}
        self.commanders: dict[str, CommanderTracker] = {}
        self.lock = threading.RLock()
        self.events_processed = 0
        self.malformed_lines = 0
        self.last_poll: datetime | None = None
        self.warnings: list[str] = []
        self.unreadable_paths: set[Path] = set()

    # ------------------------------------------------------------------
    # Session registration and diagnostics
    # ------------------------------------------------------------------

    def register_session(
        self,
        path: Path,
        *,
        offset: int = 0,
        line_number: int = 0,
        partial: bytes = b"",
        live: bool | None = None,
        reset: bool = False,
        first_seen: datetime | None = None,
    ) -> SessionState:
        path = Path(path)
        with self.lock:
            existing = self.sessions.get(path)
            if existing is not None and not reset:
                return existing
            session = SessionState(
                path=path,
                first_seen=first_seen or utcnow(),
                offset=offset,
                partial=partial,
                line_number=line_number,
                live=live,
            )
            self.sessions[path] = session
            self.unreadable_paths.discard(path)
            return session

    def session_for(self, path: Path) -> SessionState | None:
        with self.lock:
            return self.sessions.get(Path(path))

    def set_session_missing(self, session: SessionState, missing: bool) -> None:
        with self.lock:
            session.missing = missing
            if missing:
                self.unreadable_paths.add(session.path)
            else:
                self.unreadable_paths.discard(session.path)

    def note_malformed(self, count: int = 1) -> None:
        with self.lock:
            self.malformed_lines += count

    def add_warning(self, message: str) -> None:
        with self.lock:
            if message not in self.warnings:
                self.warnings.append(message)
                if len(self.warnings) > 100:
                    del self.warnings[: len(self.warnings) - 100]

    def set_last_poll(self, when: datetime | None = None) -> None:
        with self.lock:
            self.last_poll = when or utcnow()

    # ------------------------------------------------------------------
    # Event application
    # ------------------------------------------------------------------

    def apply_batch(self, envelopes: list[EventEnvelope]) -> int:
        """Sort and apply a batch of envelopes. Returns newly applied count."""

        if not envelopes:
            return 0
        ordered = sorted(
            envelopes,
            key=lambda e: (e.timestamp, e.filename, e.line_number),
        )
        applied = 0
        with self.lock:
            for envelope in ordered:
                if self._apply_envelope(envelope):
                    applied += 1
        return applied

    def _apply_envelope(self, envelope: EventEnvelope) -> bool:
        session = self.sessions.get(Path(envelope.path))
        if session is None:
            session = self.register_session(Path(envelope.path))
        session.parsed = True

        seen = False
        if envelope.line_hash:
            seen = envelope.line_hash in session.applied_hashes
            if seen and envelope.event in GAMEPLAY_EVENTS:
                return False
            session.applied_hashes.add(envelope.line_hash)

        # State events (header/commander/station) are idempotent and are
        # re-applied after a truncation, but are not counted twice.
        if not seen:
            self.events_processed += 1
        self._apply_event(session, envelope)
        return not seen

    def _apply_event(self, session: SessionState, envelope: EventEnvelope) -> None:
        event = envelope.event
        data = envelope.data
        self._touch(session, envelope.timestamp)

        if event == "Fileheader":
            major = game_major_version(data.get("gameversion"))
            if major is None:
                session.live = True
                if not session.missing_header_warned:
                    session.missing_header_warned = True
                    self.add_warning(
                        f"{session.filename}: missing Fileheader gameversion; "
                        "treating session as live"
                    )
            else:
                session.live = major >= 4
                if not session.live:
                    session.pending.clear()
            if session.live and session.tracker_key is not None:
                self._flush_pending(session)
            return

        if event == "Commander":
            self._apply_commander(session, envelope)
            return

        if event in {"Location", "Docked", "Undocked"}:
            self._apply_station(session, envelope)
            return

        # Gameplay events require both known liveness and commander identity.
        if session.live is False:
            return
        if session.live is None or session.tracker_key is None:
            session.pending.append(envelope)
            return
        self._apply_gameplay(session.tracker_key, envelope)

    def _apply_commander(
        self,
        session: SessionState,
        envelope: EventEnvelope,
    ) -> None:
        fid = as_text(envelope.data.get("FID"))
        name = as_text(envelope.data.get("Name"))
        if not fid:
            return

        if session.unknown_key is not None:
            self.merge_commanders(session.unknown_key, fid)
            session.unknown_key = None

        session.commander_fid = fid
        if name:
            session.commander_name = name
        self._get_or_create_commander(fid, fid=fid, name=name)

        if session.live is not None:
            self._flush_pending(session)

    def _apply_station(
        self,
        session: SessionState,
        envelope: EventEnvelope,
    ) -> None:
        event = envelope.event
        data = envelope.data
        if event == "Undocked":
            session.station = None
            return
        if event == "Location" and not as_bool(data.get("Docked")):
            return
        session.station = StationContext(
            system=as_text(data.get("StarSystem")),
            system_address=(
                as_int(data.get("SystemAddress"), 0)
                if data.get("SystemAddress") is not None
                else None
            ),
            station=as_text(data.get("StationName")),
        )

    def _apply_gameplay(self, key: str, envelope: EventEnvelope) -> None:
        tracker = self.commanders.get(key)
        if tracker is None:
            tracker = self._get_or_create_commander(key)
        event = envelope.event
        data = envelope.data

        if event == "MissionAccepted":
            apply_mission_accepted(
                tracker,
                data,
                envelope.timestamp,
                envelope.station,
                True,
            )
        elif event == "MissionRedirected":
            apply_mission_redirected(tracker, data)
        elif event == "MissionCompleted":
            apply_mission_completed(tracker, data)
        elif event == "MissionAbandoned":
            apply_mission_abandoned(tracker, data)
        elif event == "MissionFailed":
            apply_mission_failed(tracker, data)
        elif event == "Missions":
            reconcile_missions(tracker, data)
        elif event == "Bounty":
            apply_bounty(tracker, data, envelope.timestamp)

    def _flush_pending(self, session: SessionState) -> None:
        if session.live is None:
            return
        key = session.tracker_key
        if key is None:
            return
        pending = session.pending
        session.pending = []
        for envelope in pending:
            if session.live is False:
                break
            self._apply_gameplay(key, envelope)

    def _touch(self, session: SessionState, timestamp: datetime) -> None:
        if session.last_event_at is None or timestamp > session.last_event_at:
            session.last_event_at = timestamp

    # ------------------------------------------------------------------
    # Commander association and migration
    # ------------------------------------------------------------------

    def _get_or_create_commander(
        self,
        key: str,
        *,
        fid: str | None = None,
        name: str | None = None,
    ) -> CommanderTracker:
        tracker = self.commanders.get(key)
        if tracker is None:
            tracker = CommanderTracker(key=key, fid=fid, name=name)
            self.commanders[key] = tracker
        else:
            if fid and not tracker.fid:
                tracker.fid = fid
            if name and not tracker.name:
                tracker.name = name
        return tracker

    def merge_commanders(self, source_key: str, target_key: str) -> None:
        """Merge an unknown-key tracker into a real FID tracker."""

        if source_key == target_key:
            return
        source = self.commanders.get(source_key)
        if source is None:
            return
        target = self._get_or_create_commander(target_key, fid=target_key)
        for mission_id, mission in source.missions.items():
            if mission_id in target.missions:
                self.add_warning(
                    f"Duplicate mission {mission_id} while associating commander; "
                    "kept the existing mission"
                )
                continue
            target.missions[mission_id] = mission
        combined = list(target.bounty_feed) + list(source.bounty_feed)
        target.replace_bounty_feed(combined)
        del self.commanders[source_key]

    def resolve_unknown_sessions(
        self,
        now: datetime | None = None,
        deadline_seconds: float | None = None,
    ) -> bool:
        """Resolve unknown liveness/commander state at replay end or timeout."""

        current = now or utcnow()
        changed = False
        with self.lock:
            for session in self.sessions.values():
                elapsed = (current - session.first_seen).total_seconds()
                if deadline_seconds is not None and elapsed < deadline_seconds:
                    continue
                if not session.parsed:
                    continue

                if session.live is None:
                    session.live = True
                    changed = True
                    if not session.missing_header_warned:
                        session.missing_header_warned = True
                        self.add_warning(
                            f"{session.filename}: no Fileheader found; "
                            "treating session as live"
                        )

                if (
                    session.commander_fid is None
                    and session.unknown_key is None
                ):
                    session.unknown_key = (
                        f"{UNKNOWN_KEY_PREFIX}{session.path.name}"
                    )
                    changed = True
                    if not session.unknown_warned:
                        session.unknown_warned = True
                        self.add_warning(
                            f"{session.filename}: no Commander event found; "
                            "using an unknown commander key"
                        )

                if session.pending and session.tracker_key is not None:
                    self._flush_pending(session)
                    changed = True
        return changed

    # ------------------------------------------------------------------
    # Views and queries
    # ------------------------------------------------------------------

    def commander_keys(self) -> list[str]:
        with self.lock:
            return sorted(
                self.commanders,
                key=lambda key: (
                    self.commanders[key].fid is None,
                    self.commanders[key].display_name.casefold(),
                    key,
                ),
            )

    def commander_view(self, key: str) -> CommanderView | None:
        with self.lock:
            tracker = self.commanders.get(key)
            if tracker is None:
                return None
            sessions = tuple(
                self._session_info(session)
                for session in self.sessions.values()
                if session.tracker_key == key
            )
            stations = frozenset(
                session.station.station.casefold()
                for session in self.sessions.values()
                if session.tracker_key == key
                and not session.missing
                and session.station is not None
                and session.station.station
            )
            display = self._commander_display_name(tracker)
            return CommanderView(
                key=key,
                fid=tracker.fid,
                name=tracker.name,
                display_name=display,
                missions=tuple(
                    mission.copy() for mission in tracker.missions.values()
                ),
                bounties=tuple(
                    replace(bounty) for bounty in tracker.bounty_feed
                ),
                sessions=sessions,
                station_names=stations,
            )

    def all_session_infos(self) -> tuple[SessionInfo, ...]:
        with self.lock:
            return tuple(
                self._session_info(session)
                for session in sorted(
                    self.sessions.values(), key=lambda s: s.filename
                )
            )

    def stats(self) -> EngineStats:
        with self.lock:
            return EngineStats(
                watched_files=len(self.sessions),
                active_sessions=sum(
                    1 for session in self.sessions.values() if not session.missing
                ),
                commanders=len(self.commanders),
                events_processed=self.events_processed,
                malformed_lines=self.malformed_lines,
                unreadable_files=len(self.unreadable_paths),
                last_poll=self.last_poll,
                warnings=tuple(self.warnings),
            )

    def _session_info(self, session: SessionState) -> SessionInfo:
        station = session.station
        return SessionInfo(
            path=session.path,
            filename=session.filename,
            commander_fid=session.commander_fid,
            commander_name=session.commander_name,
            system=station.system if station else None,
            station=station.station if station else None,
            live=session.live,
            last_event_at=session.last_event_at,
            missing=session.missing,
            offset=session.offset,
            pending_count=len(session.pending),
        )

    def _commander_display_name(self, tracker: CommanderTracker) -> str:
        if tracker.name:
            return tracker.name
        if tracker.fid:
            return tracker.fid
        if tracker.key.startswith(UNKNOWN_KEY_PREFIX):
            return f"Unknown ({tracker.key[len(UNKNOWN_KEY_PREFIX):]})"
        return tracker.key

    # ------------------------------------------------------------------
    # UI actions
    # ------------------------------------------------------------------

    def adjust_mission(self, key: str, mission_id: int, delta: int) -> bool:
        with self.lock:
            tracker = self.commanders.get(key)
            if tracker is None:
                return False
            return adjust_mission(tracker, mission_id, delta)

    def adjust_all(self, key: str, delta: int) -> int:
        with self.lock:
            tracker = self.commanders.get(key)
            if tracker is None:
                return 0
            return adjust_all_stacks(tracker, delta)

    def set_mission_state(
        self,
        key: str,
        mission_id: int,
        state: MissionState,
    ) -> bool:
        with self.lock:
            tracker = self.commanders.get(key)
            if tracker is None:
                return False
            return set_manual_state(tracker, mission_id, state)

    def remove_mission(self, key: str, mission_id: int) -> bool:
        with self.lock:
            tracker = self.commanders.get(key)
            if tracker is None:
                return False
            return tracker.remove_mission(mission_id)

    def clear_completed(self, key: str) -> int:
        with self.lock:
            tracker = self.commanders.get(key)
            if tracker is None:
                return 0
            return tracker.clear_terminal()
