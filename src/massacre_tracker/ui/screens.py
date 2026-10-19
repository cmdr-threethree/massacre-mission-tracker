"""Modal screens: confirmations, help, clipboard, and startup errors."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

HELP_TEXT = """\
MASSACRE MISSION TRACKER — HELP

Mission actions (selected row)
  + / -        Increment or decrement the selected mission
  ] / [        Increment or decrement one mission in every stack
  c            Mark selected mission COMPLETED
  a            Mark selected mission ABANDONED
  f            Mark selected mission FAILED
  r            Mark selected mission ACTIVE again
  delete       Delete the selected mission from memory
  y            Add the selected mission source to the in-memory clipboard
  Y            Copy a source field or add the source triple
  ctrl+x       Clear completed/abandoned/failed missions from memory

Navigation
  ctrl+n       Switch to the next commander
  v            Switch between Active and Completed views
  s            Cycle sort key
  shift+s      Toggle sort direction
  ctrl+r       Poll the journal directory immediately
  ?            Open this help
  q            Quit

Views and colour legend
  Active view:    ACTIVE and REDIRECTED missions
  Completed view: COMPLETED, ABANDONED, and FAILED missions
  Green row:      ready to turn in at a station matching the source station
  State column:   ACTIVE=green, REDIRECTED=bright green,
                  COMPLETED=cyan, ABANDONED=yellow, FAILED=red

Notes
  All state is rebuilt from journal files at startup. Manual edits and the
  clipboard are in memory only and are discarded when the application exits.
  Legacy sessions are watched but never contribute missions or kills.
"""


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Confirm"),
    ]

    def __init__(self, prompt: str, *, confirm_label: str = "Confirm") -> None:
        super().__init__()
        self.prompt = prompt
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog", classes="dialog"):
            yield Static(self.prompt, id="confirm-prompt", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel", variant="default")
                yield Button(self.confirm_label, id="confirm", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss_screen", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog", classes="dialog wide-dialog"):
            yield Static("Help", classes="dialog-title")
            with VerticalScroll(id="help-body"):
                yield Static(HELP_TEXT, markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)


class ClipboardScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss_screen", "Close")]

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="clipboard-dialog", classes="dialog wide-dialog"):
            yield Static("Clipboard", classes="dialog-title")
            with VerticalScroll(id="clipboard-body"):
                yield Static(self.text, markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)


class CopyMenuScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss_screen", "Cancel")]

    def __init__(self, mission_label: str) -> None:
        super().__init__()
        self.mission_label = mission_label

    def compose(self) -> ComposeResult:
        with Vertical(id="copy-dialog", classes="dialog wide-dialog"):
            yield Static("Copy mission source", classes="dialog-title")
            yield Static(self.mission_label, markup=False)
            with Vertical(classes="dialog-buttons vertical-buttons"):
                yield Button("Add source triple to clipboard", id="add")
                yield Button("Copy source system", id="source_system")
                yield Button("Copy source station", id="source_station")
                yield Button("Copy destination system", id="destination_system")
                yield Button("Cancel", id="cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "cancel":
            self.dismiss(None)
        else:
            self.dismiss(button_id)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)


class MissingDirectoryScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "quit_app", "Quit")]

    def __init__(self, path: str, error: str) -> None:
        super().__init__()
        self.path = path
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(id="error-dialog", classes="dialog wide-dialog"):
            yield Static("Journal directory unavailable", classes="dialog-title")
            yield Static(
                f"Configured path:\n{self.path}\n\n{self.error}\n\n"
                "Use --journal-dir or ED_JOURNAL_DIR to point at the "
                "directory containing Journal.*.log files.",
                markup=False,
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Quit", id="quit", variant="default")
                yield Button("Retry", id="retry", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "retry":
            self.dismiss(None)
            action = getattr(self.app, "action_retry_scan", None)
            if callable(action):
                action()
        else:
            self.app.exit()

    def action_quit_app(self) -> None:
        self.app.exit()
