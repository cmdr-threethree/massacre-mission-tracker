from __future__ import annotations

import asyncio
from pathlib import Path

from massacre_tracker.config import Config
from massacre_tracker.ui.app import TrackerApp
from massacre_tracker.ui.screens import HelpScreen, MissingDirectoryScreen
from tests.builders import (
    BASE_TIME,
    bounty,
    commander,
    docked,
    fileheader,
    journal_path,
    mission_accepted,
    write_journal,
)


async def wait_for_scan(app: TrackerApp, pilot, timeout: float = 3.0) -> None:
    await asyncio.to_thread(app._scan_finished_event.wait, timeout)
    for _ in range(8):
        await pilot.pause()


def single_commander_journal(directory: Path, *, fid: str = "F100", name: str = "Jameson") -> Path:
    path = journal_path(directory, BASE_TIME, 1)
    write_journal(
        path,
        [
            fileheader(),
            commander(fid=fid, name=name),
            docked(),
            mission_accepted(1, kills=3),
        ],
    )
    return path


def test_app_mounts_and_scans_empty_directory(tmp_path: Path):
    async def scenario() -> None:
        app = TrackerApp(Config(journal_dir=tmp_path))
        async with app.run_test(size=(100, 30)) as pilot:
            await wait_for_scan(app, pilot)
            assert app.scan_complete
            assert app.scan_report is not None
            assert app.scan_report.files_total == 0
            assert app.current_key is None
            assert app.query_one("#missions").row_count == 0
            app.action_quit()
            await pilot.pause()

    asyncio.run(scenario())


def test_missing_directory_shows_error_screen(tmp_path: Path):
    async def scenario() -> None:
        missing = tmp_path / "does-not-exist"
        app = TrackerApp(Config(journal_dir=missing))
        async with app.run_test(size=(100, 30)) as pilot:
            await wait_for_scan(app, pilot)
            for _ in range(10):
                if isinstance(app.screen, MissingDirectoryScreen):
                    break
                await pilot.pause()
            assert isinstance(app.screen, MissingDirectoryScreen)
            app.pop_screen()
            await pilot.pause()
            app.action_quit()
            await pilot.pause()

    asyncio.run(scenario())


def test_populated_ui_and_keyboard_actions(tmp_path: Path):
    single_commander_journal(tmp_path)

    async def scenario() -> None:
        app = TrackerApp(Config(journal_dir=tmp_path))
        async with app.run_test(size=(100, 30)) as pilot:
            await wait_for_scan(app, pilot)
            assert app.current_key == "F100"
            assert app._commander_options_sig == (("Jameson", "F100"),)
            session = app.engine.all_session_infos()[0]
            assert TrackerApp._session_commander_label(session) == "Jameson"
            assert str(tmp_path) not in str(app.query_one("#status-line").content)
            table = app.query_one("#missions")
            assert table.row_count == 1
            assert app.selected_mission_id == 1
            table.focus()
            await pilot.pause()

            await pilot.press("+")
            await pilot.pause()
            assert app.engine.commander_view("F100").missions[0].progress_kills == 1

            await pilot.press("-")
            await pilot.pause()
            assert app.engine.commander_view("F100").missions[0].progress_kills == 0

            await pilot.press("s")
            await pilot.pause()
            assert app.sort_field == "system"
            await pilot.press("shift+s")
            await pilot.pause()
            assert app.sort_ascending is False

            await pilot.press("v")
            await pilot.pause()
            assert app.view_mode == "completed"
            assert table.row_count == 0
            await pilot.press("v")
            await pilot.pause()
            assert app.view_mode == "active"
            assert table.row_count == 1

            await pilot.press("y")
            await pilot.pause()
            assert len(app.clipboard_store.entries) == 1

            await pilot.press("?")
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()

            await pilot.press("ctrl+r")
            await pilot.pause()
            app.action_quit()
            await pilot.pause()

    asyncio.run(scenario())


def test_two_commanders_and_session_panel(tmp_path: Path):
    path_a = journal_path(tmp_path, BASE_TIME, 1)
    write_journal(
        path_a,
        [fileheader(), commander(fid="F100", name="A"), docked(), mission_accepted(1)],
    )
    path_b = journal_path(tmp_path, BASE_TIME, 2)
    write_journal(
        path_b,
        [fileheader(), commander(fid="F200", name="B"), docked(station="Fisher", system="LHS 20"), mission_accepted(2)],
    )

    async def scenario() -> None:
        app = TrackerApp(Config(journal_dir=tmp_path))
        async with app.run_test(size=(80, 24)) as pilot:
            await wait_for_scan(app, pilot)
            assert set(app.engine.commander_keys()) == {"F100", "F200"}
            assert app.current_key == "F100"
            assert app.query_one("#sessions-table").row_count == 2
            first = app.current_key
            app.query_one("#missions").focus()
            await pilot.press("ctrl+n")
            await pilot.pause()
            assert app.current_key != first
            app.action_quit()
            await pilot.pause()

    asyncio.run(scenario())


def test_live_append_updates_ui(tmp_path: Path):
    path = journal_path(tmp_path, BASE_TIME, 1)
    write_journal(path, [fileheader(), commander(), docked()])

    async def scenario() -> None:
        app = TrackerApp(Config(journal_dir=tmp_path, poll_interval=0.1))
        async with app.run_test(size=(100, 30)) as pilot:
            await wait_for_scan(app, pilot)
            assert app.query_one("#missions").row_count == 0
            with path.open("a", encoding="utf-8") as handle:
                handle.write(mission_accepted(1) + "\n")
            for _ in range(40):
                await asyncio.sleep(0.1)
                await pilot.pause()
                if app.query_one("#missions").row_count == 1:
                    break
            assert app.query_one("#missions").row_count == 1
            app.action_quit()
            await pilot.pause()

    asyncio.run(scenario())


def test_no_color_option_installs_monochrome_filter(tmp_path: Path):
    app = TrackerApp(Config(journal_dir=tmp_path, color=False))
    assert app.no_color is True
    assert any(type(item).__name__ == "Monochrome" for item in app._filters)


def test_commander_filter_selects_initial_view(tmp_path: Path):
    path_a = journal_path(tmp_path, BASE_TIME, 1)
    write_journal(
        path_a,
        [fileheader(), commander(fid="F100", name="Alpha"), docked(), mission_accepted(1)],
    )
    path_b = journal_path(tmp_path, BASE_TIME, 2)
    write_journal(
        path_b,
        [fileheader(), commander(fid="F200", name="Beta"), docked(station="Fisher", system="LHS 20"), mission_accepted(2)],
    )

    async def scenario() -> None:
        app = TrackerApp(Config(journal_dir=tmp_path, commander_filter="Beta"))
        async with app.run_test(size=(100, 30)) as pilot:
            await wait_for_scan(app, pilot)
            assert app.current_key == "F200"
            assert app.query_one("#missions").row_count == 1
            app.action_quit()
            await pilot.pause()

    asyncio.run(scenario())


def test_bounty_is_reflected_in_ui_feed(tmp_path: Path):
    path = single_commander_journal(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(bounty() + "\n")

    async def scenario() -> None:
        app = TrackerApp(Config(journal_dir=tmp_path))
        async with app.run_test(size=(100, 30)) as pilot:
            await wait_for_scan(app, pilot)
            assert app.query_one("#bounties-table").row_count == 1
            view = app.engine.commander_view("F100")
            assert view is not None
            assert view.missions[0].progress_kills == 1
            app.action_quit()
            await pilot.pause()

    asyncio.run(scenario())
