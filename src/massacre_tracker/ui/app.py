"""Textual terminal user interface."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.filter import Monochrome
from textual.message import Message
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from ..config import Config
from ..domain.models import Bounty, Mission, MissionState
from ..engine import CommanderView, SessionInfo, TrackerEngine
from ..journal.discovery import JournalDirectoryError
from ..journal.scanner import (
    JournalScanner,
    ScanProgress as ScanProgressInfo,
    ScanReport,
)
from ..journal.tailer import JournalTailer
from .clipboard import ClipboardStore
from .formatting import (
    format_age,
    format_credits,
    format_expiry,
    format_local_time,
    format_ship,
    format_wait,
    is_ready,
    mission_kills,
    state_color,
)
from .screens import (
    ClipboardScreen,
    ConfirmScreen,
    CopyMenuScreen,
    HelpScreen,
    MissingDirectoryScreen,
)

MISSION_COLUMNS = (
    "SYSTEM",
    "STATION",
    "ISSUING FACTION",
    "TARGET FACTION",
    "KILLS",
    "REWARD",
    "EXPIRES",
    "WING",
    "STATE",
)
STACK_COLUMNS = (
    "ISSUING FACTION",
    "TARGET FACTION",
    "MISSIONS",
    "CONTRACT KILLS",
    "DEFICIT",
    "REWARD",
)
TARGET_COLUMNS = (
    "TARGET FACTION",
    "MISSIONS",
    "ACTIVE",
    "LARGEST STACK",
    "TOTAL CONTRACT",
    "OVERLAP",
    "REMAINING",
    "KILLS TO NEXT",
    "TOTAL REWARD",
    "SHAREABLE",
    "TURN-IN",
    "SHARE TURN-IN",
    "AVG / UNIQUE KILL",
    "AVG / MISSION",
)
BOUNTY_COLUMNS = ("TARGET SHIP", "VICTIM FACTION", "REWARD", "SINCE PREVIOUS")
SESSION_COLUMNS = (
    "JOURNAL FILE",
    "COMMANDER",
    "STATION",
    "SYSTEM",
    "LAST EVENT",
    "STATE",
)

SORT_FIELDS: tuple[tuple[str, str], ...] = (
    ("default", "Default"),
    ("system", "System"),
    ("station", "Station"),
    ("issuer", "Issuing faction"),
    ("target", "Target faction"),
    ("contract", "Contract kills"),
    ("reward", "Reward"),
    ("expiry", "Expiry"),
    ("wing", "Wing"),
)
SORT_LABELS = dict(SORT_FIELDS)

VIEW_ACTIVE = "active"
VIEW_COMPLETED = "completed"


class ScanProgressMessage(Message):
    def __init__(self, progress: ScanProgressInfo) -> None:
        super().__init__()
        self.progress = progress


class ScanFinishedMessage(Message):
    def __init__(self, report: ScanReport) -> None:
        super().__init__()
        self.report = report


class ScanFailedMessage(Message):
    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error


class EngineUpdated(Message):
    """The background tailer has new state or a new poll timestamp."""


class TailerErrorMessage(Message):
    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error


class TrackerApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "Massacre Mission Tracker"
    SUB_TITLE = "Live journal tracking"

    BINDINGS = [
        Binding("+", "increment_selected", "Kill +", show=True),
        Binding("-", "decrement_selected", "Kill -", show=True),
        Binding("]", "increment_all", "All +", show=False),
        Binding("[", "decrement_all", "All -", show=False),
        Binding("c", "mark_completed", "Complete", show=True),
        Binding("a", "mark_abandoned", "Abandon", show=True),
        Binding("f", "mark_failed", "Fail", show=True),
        Binding("r", "mark_active", "Reactivate", show=True),
        Binding("delete", "delete_selected", "Delete", show=False),
        Binding("y", "add_clipboard", "Clip", show=False),
        Binding("shift+y", "copy_menu", "Copy", show=False),
        Binding("ctrl+y", "show_clipboard", "Clipboard", show=False),
        Binding("ctrl+x", "clear_completed", "Clear done", show=False),
        Binding("ctrl+n", "next_commander", "Commander", show=True),
        Binding("v", "cycle_view", "View", show=True),
        Binding("s", "cycle_sort", "Sort", show=True),
        Binding("shift+s", "toggle_sort_direction", "Direction", show=False),
        Binding("ctrl+r", "poll_now", "Poll now", show=True),
        Binding("?", "help", "Help", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        config: Config,
        engine: TrackerEngine | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        if not config.color:
            self.no_color = True
            if not any(type(item).__name__ == "Monochrome" for item in self._filters):
                self._filters.append(Monochrome())
        self.engine = engine or TrackerEngine()
        self.scanner = JournalScanner(self.engine, config)
        self.tailer: JournalTailer | None = None
        self.clipboard_store = ClipboardStore()

        self.current_key: str | None = None
        self.selected_mission_id: int | None = None
        self.view_mode = VIEW_ACTIVE
        self.sort_field = "default"
        self.sort_ascending = True

        self.scan_report: ScanReport | None = None
        self.scan_error: str | None = None
        self.scan_progress_text = "Starting journal scan"
        self.scan_complete = False

        self._scan_thread: threading.Thread | None = None
        self._tail_thread: threading.Thread | None = None
        self._scan_finished_event = threading.Event()
        self._exiting = False
        self._updating_selectors = False
        self._commander_options_sig: tuple[tuple[str, str], ...] = ()
        self._filter_warning_shown = False
        self._pending_delete: tuple[str, int] | None = None
        self._pending_clear_key: str | None = None
        self._copy_mission_id: int | None = None
        self._copy_commander_key: str | None = None

    # ------------------------------------------------------------------
    # Composition and lifecycle
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="status-bar"):
            yield Static("", id="status-line", markup=False)
            yield Static("", id="warnings-line", markup=False)
        with Horizontal(id="controls"):
            yield Static("Commander", classes="control-label")
            yield Select(
                [],
                prompt="No commander",
                allow_blank=True,
                type_to_search=False,
                id="commander-select",
            )
            yield Static("View", classes="control-label")
            yield Select(
                [("Active", VIEW_ACTIVE), ("Completed", VIEW_COMPLETED)],
                allow_blank=False,
                value=VIEW_ACTIVE,
                type_to_search=False,
                id="view-select",
            )
            yield Static("Sort: Default ↑", id="sort-label", markup=False)
        yield Static("", id="empty-message", markup=False, classes="hidden")
        yield DataTable(id="missions", cursor_type="row", zebra_stripes=True)
        with TabbedContent(id="details"):
            with TabPane("Stacks", id="tab-stacks"):
                yield DataTable(id="stacks-table", cursor_type="row")
            with TabPane("Targets", id="tab-targets"):
                yield DataTable(id="targets-table", cursor_type="row")
            with TabPane("Bounties", id="tab-bounties"):
                yield DataTable(id="bounties-table", cursor_type="row")
            with TabPane("Sessions", id="tab-sessions"):
                yield DataTable(id="sessions-table", cursor_type="row")
            with TabPane("Clipboard", id="tab-clipboard"):
                yield Static("", id="clipboard-text", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._init_tables()
        self._refresh_ui()
        self._start_scan()

    def on_unmount(self) -> None:
        self._exiting = True
        self._stop_tailer()

    # ------------------------------------------------------------------
    # Background workers
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        if self._scan_thread is not None and self._scan_thread.is_alive():
            return
        self.scan_error = None
        self.scan_report = None
        self.scan_complete = False
        self.scan_progress_text = "Scanning journals"
        self._scan_finished_event.clear()
        self._scan_thread = threading.Thread(
            target=self._scan_worker,
            name="journal-scan",
            daemon=True,
        )
        self._scan_thread.start()

    def _scan_worker(self) -> None:
        try:
            report = self.scanner.scan(progress=self._post_scan_progress)
        except JournalDirectoryError as exc:
            self._scan_finished_event.set()
            self._post(ScanFailedMessage(str(exc)))
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            self._scan_finished_event.set()
            self._post(ScanFailedMessage(f"Journal scan failed: {exc}"))
        else:
            self._scan_finished_event.set()
            self._post(ScanFinishedMessage(report))

    def _post_scan_progress(self, progress: ScanProgressInfo) -> None:
        self._post(ScanProgressMessage(progress))

    def _post(self, message: Message) -> None:
        if self._exiting:
            return
        try:
            self.post_message(message)
        except Exception:  # pragma: no cover - app may be shutting down
            pass

    def _start_tailer(self) -> None:
        if self._tail_thread is not None and self._tail_thread.is_alive():
            return
        self.tailer = JournalTailer(self.engine, self.config)
        self._tail_thread = threading.Thread(
            target=self._tail_worker,
            name="journal-tail",
            daemon=True,
        )
        self._tail_thread.start()

    def _tail_worker(self) -> None:
        tailer = self.tailer
        if tailer is None:
            return
        try:
            tailer.run_forever(
                on_cycle=lambda _result: self._post(EngineUpdated())
            )
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            if not tailer.stop_event.is_set():
                self._post(TailerErrorMessage(str(exc)))

    def _stop_tailer(self) -> None:
        if self.tailer is not None:
            self.tailer.stop()
        if self._tail_thread is not None and self._tail_thread.is_alive():
            self._tail_thread.join(timeout=1.0)
        self._tail_thread = None
        self.tailer = None

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def on_scan_progress_message(self, message: ScanProgressMessage) -> None:
        progress = message.progress
        self.scan_progress_text = (
            f"Scanning {progress.files_done}/{progress.files_total} files "
            f"({progress.lines_read:,} lines; {progress.current_file})"
        )
        self._update_status_text(self.engine.stats())

    def on_scan_finished_message(self, message: ScanFinishedMessage) -> None:
        self.scan_report = message.report
        self.scan_complete = True
        self._refresh_ui()
        self._start_tailer()

    def on_scan_failed_message(self, message: ScanFailedMessage) -> None:
        self.scan_error = message.error
        self.scan_complete = True
        self.scan_progress_text = "Journal scan failed"
        self._update_status_text(self.engine.stats())
        self.push_screen(
            MissingDirectoryScreen(str(self.config.journal_dir), message.error)
        )

    def on_engine_updated(self, _message: EngineUpdated) -> None:
        self._refresh_ui()

    def on_tailer_error_message(self, message: TailerErrorMessage) -> None:
        self.engine.add_warning(f"Journal tailer stopped: {message.error}")
        self.notify(
            f"Journal tailer stopped: {message.error}",
            title="Tailer error",
            severity="error",
            timeout=10,
        )
        self._refresh_ui()

    # ------------------------------------------------------------------
    # Widget setup and refresh
    # ------------------------------------------------------------------

    def _init_tables(self) -> None:
        self.query_one("#missions", DataTable).add_columns(*MISSION_COLUMNS)
        self.query_one("#stacks-table", DataTable).add_columns(*STACK_COLUMNS)
        self.query_one("#targets-table", DataTable).add_columns(*TARGET_COLUMNS)
        self.query_one("#bounties-table", DataTable).add_columns(*BOUNTY_COLUMNS)
        self.query_one("#sessions-table", DataTable).add_columns(*SESSION_COLUMNS)

    def _refresh_ui(self) -> None:
        stats = self.engine.stats()
        keys = self.engine.commander_keys()
        if keys and self.current_key not in keys:
            self.current_key = self._choose_initial_commander(keys)
        elif not keys:
            self.current_key = None

        self._sync_commander_select(keys)
        self._sync_view_select()
        self._update_status_text(stats)
        self._update_sort_label()

        view = (
            self.engine.commander_view(self.current_key)
            if self.current_key is not None
            else None
        )
        self._refresh_missions_table(view)
        self._refresh_stacks(view)
        self._refresh_targets(view)
        self._refresh_bounties(view)
        self._refresh_sessions()
        self._refresh_clipboard()
        self._refresh_empty_message(view)

    def _choose_initial_commander(self, keys: list[str]) -> str:
        wanted = self.config.commander_filter
        if wanted:
            wanted_cf = wanted.casefold()
            for key in keys:
                view = self.engine.commander_view(key)
                candidates = [key]
                if view is not None:
                    candidates.extend([view.fid or "", view.name or ""])
                if any(
                    candidate and candidate.casefold() == wanted_cf
                    for candidate in candidates
                ):
                    return key
            if not self._filter_warning_shown:
                self._filter_warning_shown = True
                self.notify(
                    f"No commander matches {wanted!r}; showing the first commander",
                    severity="warning",
                )
        return keys[0]

    def _sync_commander_select(self, keys: list[str]) -> None:
        options: list[tuple[str, str]] = []
        for key in keys:
            view = self.engine.commander_view(key)
            if view is None:
                options.append((key, key))
            elif view.name and view.fid:
                options.append((f"{view.name} ({view.fid})", key))
            else:
                options.append((view.display_name, key))

        signature = tuple(options)
        select = self.query_one("#commander-select", Select)
        if signature != self._commander_options_sig:
            self._updating_selectors = True
            try:
                select.set_options(options)
            finally:
                self._updating_selectors = False
            self._commander_options_sig = signature

        if self.current_key is None:
            if select.value is not Select.NULL:
                self._updating_selectors = True
                try:
                    select.clear()
                finally:
                    self._updating_selectors = False
        elif select.value != self.current_key:
            self._updating_selectors = True
            try:
                select.value = self.current_key
            finally:
                self._updating_selectors = False
        select.disabled = not keys

    def _sync_view_select(self) -> None:
        select = self.query_one("#view-select", Select)
        if select.value != self.view_mode:
            self._updating_selectors = True
            try:
                select.value = self.view_mode
            finally:
                self._updating_selectors = False

    def _update_status_text(self, stats) -> None:
        if self.scan_error:
            scan_text = "Journal scan failed"
        elif not self.scan_complete:
            scan_text = self.scan_progress_text
        elif self.scan_report is None:
            scan_text = "Scan complete"
        elif self.scan_report.files_total == 0:
            scan_text = "No journal files found; polling continues"
        else:
            report = self.scan_report
            scan_text = (
                f"Reconstructed {report.files_included}/{report.files_total} "
                f"files ({report.files_excluded} outside window)"
            )

        status = (
            f"Scan: {scan_text}  |  files {stats.watched_files}"
            f"  |  sessions {stats.active_sessions}"
            f"  |  cmdrs {stats.commanders}"
            f"  |  poll {format_local_time(stats.last_poll)}"
            f"  |  events {stats.events_processed:,}"
            f"  |  malformed {stats.malformed_lines}"
        )
        path_line = f"Journal directory: {self.config.journal_dir}"
        self.query_one("#status-line", Static).update(path_line + "\n" + status)

        warning_parts: list[str] = []
        if self.scan_error:
            warning_parts.append(self.scan_error)
        if stats.unreadable_files:
            warning_parts.append(f"{stats.unreadable_files} unreadable/missing file(s)")
        if stats.warnings:
            warning_parts.append(stats.warnings[-1])
        if stats.malformed_lines:
            warning_parts.append(f"{stats.malformed_lines} malformed line(s) skipped")
        self.query_one("#warnings-line", Static).update(
            "  |  ".join(warning_parts) if warning_parts else "No warnings"
        )

    def _update_sort_label(self) -> None:
        arrow = "↑" if self.sort_ascending else "↓"
        self.query_one("#sort-label", Static).update(
            f"Sort: {SORT_LABELS[self.sort_field]} {arrow}"
        )

    # ------------------------------------------------------------------
    # Mission table
    # ------------------------------------------------------------------

    def _visible_missions(self, view: CommanderView) -> list[Mission]:
        if self.view_mode == VIEW_ACTIVE:
            missions = [
                mission
                for mission in view.missions
                if mission.state in (MissionState.ACTIVE, MissionState.REDIRECTED)
            ]
        else:
            missions = [
                mission for mission in view.missions if mission.is_terminal
            ]
        return self._sort_missions(missions)

    def _refresh_missions_table(self, view: CommanderView | None) -> None:
        table = self.query_one("#missions", DataTable)
        table.clear()
        if view is None:
            self.selected_mission_id = None
            return

        missions = self._visible_missions(view)
        visible_ids = {mission.mission_id for mission in missions}
        if self.selected_mission_id not in visible_ids:
            self.selected_mission_id = missions[0].mission_id if missions else None

        for mission in missions:
            ready = is_ready(mission, view.station_names)
            row_style = "bold bright_green" if ready else ""
            wing_text = Text("WING", style="bold yellow") if mission.wing else Text("—")
            state_text = Text(
                mission.state.value,
                style=state_color(mission.state),
            )
            cells = [
                self._cell(mission.source_system or "—", row_style),
                self._cell(mission.source_station or "—", row_style),
                self._cell(mission.issuing_faction or "—", row_style),
                self._cell(mission.target_faction or "—", row_style),
                self._cell(mission_kills(mission), row_style),
                self._cell(format_credits(mission.reward), row_style),
                self._cell(format_expiry(mission.expires_at), row_style),
                wing_text,
                state_text,
            ]
            table.add_row(*cells, key=str(mission.mission_id))

        if self.selected_mission_id is not None and missions:
            try:
                index = table.get_row_index(str(self.selected_mission_id))
                table.move_cursor(row=index)
            except Exception:
                pass

    @staticmethod
    def _cell(value: str, style: str = "") -> Text:
        return Text(value, style=style) if style else Text(value)

    def _sort_missions(self, missions: list[Mission]) -> list[Mission]:
        field = self.sort_field
        reverse = not self.sort_ascending

        if field in {"default", "expiry"}:
            with_expiry = [m for m in missions if m.expires_at is not None]
            without_expiry = [m for m in missions if m.expires_at is None]
            if field == "default":
                key = lambda m: (
                    (m.source_system or "").casefold(),
                    (m.source_station or "").casefold(),
                    (m.issuing_faction or "").casefold(),
                    m.expires_at,
                    m.mission_id,
                )
                unknown_key = lambda m: (
                    (m.source_system or "").casefold(),
                    (m.source_station or "").casefold(),
                    (m.issuing_faction or "").casefold(),
                    m.accepted_at,
                    m.mission_id,
                )
            else:
                key = lambda m: (m.expires_at, m.mission_id)
                unknown_key = lambda m: (m.accepted_at, m.mission_id)
            with_expiry.sort(key=key, reverse=reverse)
            without_expiry.sort(key=unknown_key, reverse=reverse)
            return with_expiry + without_expiry

        key_functions = {
            "system": lambda m: ((m.source_system or "").casefold(), m.mission_id),
            "station": lambda m: ((m.source_station or "").casefold(), m.mission_id),
            "issuer": lambda m: ((m.issuing_faction or "").casefold(), m.mission_id),
            "target": lambda m: ((m.target_faction or "").casefold(), m.mission_id),
            "contract": lambda m: (m.contract_kills, m.mission_id),
            "reward": lambda m: (m.reward, m.mission_id),
            "wing": lambda m: (m.wing, m.mission_id),
        }
        return sorted(missions, key=key_functions[field], reverse=reverse)

    # ------------------------------------------------------------------
    # Detail panels
    # ------------------------------------------------------------------

    def _refresh_stacks(self, view: CommanderView | None) -> None:
        table = self.query_one("#stacks-table", DataTable)
        table.clear()
        if view is None:
            return
        for stack in view.stack_summaries():
            table.add_row(
                stack.issuing_faction or "—",
                stack.target_faction or "—",
                str(stack.mission_count),
                str(stack.contract_kills),
                str(stack.deficit),
                format_credits(stack.reward),
            )

    def _refresh_targets(self, view: CommanderView | None) -> None:
        table = self.query_one("#targets-table", DataTable)
        table.clear()
        if view is None:
            return
        for summary in view.target_summaries():
            table.add_row(
                summary.target_faction or "—",
                str(summary.mission_count),
                str(summary.active_mission_count),
                str(summary.max_stack_contract),
                str(summary.total_contract),
                f"{summary.overlap_ratio:.2f}",
                str(summary.remaining_required),
                str(summary.kills_to_next_completion),
                format_credits(summary.total_reward),
                format_credits(summary.shareable_reward),
                format_credits(summary.turn_in_reward),
                format_credits(summary.shareable_turn_in_reward),
                format_credits(summary.average_per_unique_kill),
                format_credits(summary.average_per_mission),
            )

    def _refresh_bounties(self, view: CommanderView | None) -> None:
        table = self.query_one("#bounties-table", DataTable)
        table.clear()
        if view is None:
            return
        for bounty in reversed(view.bounties):
            table.add_row(*self._bounty_row(bounty))

    @staticmethod
    def _bounty_row(bounty: Bounty) -> tuple[str, ...]:
        return (
            format_ship(bounty.ship_type),
            bounty.victim_faction or "—",
            format_credits(bounty.reward),
            format_wait(bounty.delta_since_previous),
        )

    def _refresh_sessions(self) -> None:
        table = self.query_one("#sessions-table", DataTable)
        table.clear()
        now = datetime.now(timezone.utc)
        for session in self.engine.all_session_infos():
            table.add_row(*self._session_row(session, now))

    def _session_row(
        self,
        session: SessionInfo,
        now: datetime,
    ) -> tuple[str, ...]:
        commander = self._session_commander_label(session)
        liveness = (
            "LIVE"
            if session.live is True
            else "LEGACY"
            if session.live is False
            else "UNKNOWN"
        )
        if session.missing:
            state = "MISSING"
        elif (
            session.last_event_at is not None
            and (now - session.last_event_at).total_seconds() < 300
        ):
            state = f"{liveness}/ACTIVE"
        else:
            state = f"{liveness}/IDLE"
        return (
            session.filename,
            commander,
            session.station or "—",
            session.system or "—",
            format_age(session.last_event_at, now),
            state,
        )

    @staticmethod
    def _session_commander_label(session: SessionInfo) -> str:
        if session.commander_name and session.commander_fid:
            return f"{session.commander_name} ({session.commander_fid})"
        if session.commander_name:
            return session.commander_name
        if session.commander_fid:
            return session.commander_fid
        return "—"

    def _refresh_clipboard(self) -> None:
        self.query_one("#clipboard-text", Static).update(
            self.clipboard_store.render()
        )

    def _refresh_empty_message(self, view: CommanderView | None) -> None:
        widget = self.query_one("#empty-message", Static)
        if view is None:
            if not self.engine.all_session_infos():
                message = (
                    "No journal files found yet. The directory is being polled "
                    "for new Journal.*.log files."
                )
            else:
                message = (
                    "No commander data yet. Waiting for Fileheader, Commander, and "
                    "MissionAccepted events."
                )
        else:
            visible = self._visible_missions(view)
            if visible:
                widget.add_class("hidden")
                return
            if self.view_mode == VIEW_ACTIVE:
                message = (
                    "No active missions. Dock at a station and accept a pirate "
                    "massacre mission to begin tracking."
                )
            else:
                message = "No completed, abandoned, or failed missions recorded."
        widget.update(message)
        widget.remove_class("hidden")

    # ------------------------------------------------------------------
    # Select and table event handlers
    # ------------------------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._updating_selectors:
            return
        if event.select.id == "commander-select":
            if isinstance(event.value, str):
                self.current_key = event.value
                self.selected_mission_id = None
                self._refresh_ui()
        elif event.select.id == "view-select":
            if isinstance(event.value, str):
                self.view_mode = event.value
                self.selected_mission_id = None
                self._refresh_ui()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "missions":
            return
        value = event.row_key.value
        if value is not None:
            try:
                self.selected_mission_id = int(value)
            except (TypeError, ValueError):
                pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "missions":
            return
        value = event.row_key.value
        if value is not None:
            try:
                self.selected_mission_id = int(value)
            except (TypeError, ValueError):
                pass

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _current_view(self) -> CommanderView | None:
        if self.current_key is None:
            return None
        return self.engine.commander_view(self.current_key)

    def _selected_mission(self) -> tuple[CommanderView, Mission] | None:
        view = self._current_view()
        if view is None or self.selected_mission_id is None:
            self.notify("Select a mission first", severity="warning")
            return None
        for mission in view.missions:
            if mission.mission_id == self.selected_mission_id:
                return view, mission
        self.notify("Selected mission is no longer present", severity="warning")
        return None

    # ------------------------------------------------------------------
    # Mission actions
    # ------------------------------------------------------------------

    def action_increment_selected(self) -> None:
        selected = self._selected_mission()
        if selected is None:
            return
        view, mission = selected
        if not self.engine.adjust_mission(view.key, mission.mission_id, 1):
            self.notify(
                "Cannot adjust a completed mission", severity="warning"
            )
            return
        self._refresh_ui()

    def action_decrement_selected(self) -> None:
        selected = self._selected_mission()
        if selected is None:
            return
        view, mission = selected
        if not self.engine.adjust_mission(view.key, mission.mission_id, -1):
            self.notify(
                "Cannot adjust a completed mission", severity="warning"
            )
            return
        self._refresh_ui()

    def action_increment_all(self) -> None:
        view = self._current_view()
        if view is None:
            self.notify("No commander selected", severity="warning")
            return
        changed = self.engine.adjust_all(view.key, 1)
        self.notify(f"Incremented {changed} stack(s)")
        self._refresh_ui()

    def action_decrement_all(self) -> None:
        view = self._current_view()
        if view is None:
            self.notify("No commander selected", severity="warning")
            return
        changed = self.engine.adjust_all(view.key, -1)
        self.notify(f"Decremented {changed} stack(s)")
        self._refresh_ui()

    def _mark_selected(self, state: MissionState, label: str) -> None:
        selected = self._selected_mission()
        if selected is None:
            return
        view, mission = selected
        if self.engine.set_mission_state(view.key, mission.mission_id, state):
            self.notify(f"Mission {mission.mission_id} marked {label}")
            self.selected_mission_id = None
            self._refresh_ui()

    def action_mark_completed(self) -> None:
        self._mark_selected(MissionState.COMPLETED, "COMPLETED")

    def action_mark_abandoned(self) -> None:
        self._mark_selected(MissionState.ABANDONED, "ABANDONED")

    def action_mark_failed(self) -> None:
        self._mark_selected(MissionState.FAILED, "FAILED")

    def action_mark_active(self) -> None:
        self._mark_selected(MissionState.ACTIVE, "ACTIVE")

    def action_delete_selected(self) -> None:
        selected = self._selected_mission()
        if selected is None:
            return
        view, mission = selected
        self._pending_delete = (view.key, mission.mission_id)
        self.push_screen(
            ConfirmScreen(
                f"Delete mission {mission.mission_id} from memory?\n\n"
                f"{mission.issuing_faction} → {mission.target_faction}\n\n"
                "This does not modify the journal files.",
                confirm_label="Delete",
            ),
            self._delete_confirmed,
        )

    def _delete_confirmed(self, confirmed: bool | None) -> None:
        pending = self._pending_delete
        self._pending_delete = None
        if not confirmed or pending is None:
            return
        key, mission_id = pending
        if self.engine.remove_mission(key, mission_id):
            self.notify(f"Mission {mission_id} removed from memory")
            self.selected_mission_id = None
            self._refresh_ui()

    def action_clear_completed(self) -> None:
        view = self._current_view()
        if view is None:
            return
        self._pending_clear_key = view.key
        self.push_screen(
            ConfirmScreen(
                "Clear completed, abandoned, and failed missions from memory?\n\n"
                "They will be reconstructed again on the next restart.",
                confirm_label="Clear",
            ),
            self._clear_confirmed,
        )

    def _clear_confirmed(self, confirmed: bool | None) -> None:
        key = self._pending_clear_key
        self._pending_clear_key = None
        if not confirmed or key is None:
            return
        count = self.engine.clear_completed(key)
        self.notify(f"Cleared {count} completed mission(s)")
        self.selected_mission_id = None
        self._refresh_ui()

    # ------------------------------------------------------------------
    # Clipboard actions
    # ------------------------------------------------------------------

    def action_add_clipboard(self) -> None:
        selected = self._selected_mission()
        if selected is None:
            return
        _view, mission = selected
        if self.clipboard_store.add_source(mission):
            self.notify("Mission source added to the in-memory clipboard")
        else:
            self.notify("Mission source is already in the clipboard")
        self._refresh_clipboard()

    def action_copy_menu(self) -> None:
        selected = self._selected_mission()
        if selected is None:
            return
        view, mission = selected
        self._copy_commander_key = view.key
        self._copy_mission_id = mission.mission_id
        label = (
            f"Mission {mission.mission_id}: {mission.issuing_faction} → "
            f"{mission.target_faction}"
        )
        self.push_screen(CopyMenuScreen(label), self._copy_choice)

    def _copy_choice(self, choice: str | None) -> None:
        if choice is None:
            return
        key = self._copy_commander_key
        mission_id = self._copy_mission_id
        self._copy_commander_key = None
        self._copy_mission_id = None
        if key is None or mission_id is None:
            return
        view = self.engine.commander_view(key)
        if view is None:
            return
        mission = next(
            (m for m in view.missions if m.mission_id == mission_id), None
        )
        if mission is None:
            self.notify("Selected mission is no longer present", severity="warning")
            return

        if choice == "add":
            if self.clipboard_store.add_source(mission):
                self.notify("Mission source added to the in-memory clipboard")
            else:
                self.notify("Mission source is already in the clipboard")
            self._refresh_clipboard()
            return

        values = {
            "source_system": mission.source_system,
            "source_station": mission.source_station,
            "destination_system": mission.destination_system,
        }
        value = values.get(choice)
        if not value:
            self.notify("That field is not available for this mission", severity="warning")
            return
        self.clipboard_store.copy(value)
        try:
            self.copy_to_clipboard(value)
        except Exception:
            pass
        self.notify(f"Copied: {value}")
        self._refresh_clipboard()

    def action_show_clipboard(self) -> None:
        self.push_screen(ClipboardScreen(self.clipboard_store.render()))

    # ------------------------------------------------------------------
    # Navigation and app actions
    # ------------------------------------------------------------------

    def action_next_commander(self) -> None:
        keys = self.engine.commander_keys()
        if not keys:
            self.notify("No commanders available", severity="warning")
            return
        if self.current_key not in keys:
            self.current_key = keys[0]
        else:
            index = keys.index(self.current_key)
            self.current_key = keys[(index + 1) % len(keys)]
        self.selected_mission_id = None
        self._refresh_ui()

    def action_cycle_view(self) -> None:
        self.view_mode = (
            VIEW_COMPLETED if self.view_mode == VIEW_ACTIVE else VIEW_ACTIVE
        )
        self.selected_mission_id = None
        self._refresh_ui()

    def action_cycle_sort(self) -> None:
        fields = [field for field, _label in SORT_FIELDS]
        index = fields.index(self.sort_field)
        self.sort_field = fields[(index + 1) % len(fields)]
        self._refresh_ui()

    def action_toggle_sort_direction(self) -> None:
        self.sort_ascending = not self.sort_ascending
        self._refresh_ui()

    def action_poll_now(self) -> None:
        if self.tailer is None:
            self.notify("The journal scan is still running", severity="warning")
            return
        self.tailer.poll_now()
        self.notify("Journal poll requested")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_retry_scan(self) -> None:
        if self._tail_thread is not None and self._tail_thread.is_alive():
            return
        self._start_scan()

    def action_quit(self) -> None:
        self._exiting = True
        self._stop_tailer()
        self.exit()
