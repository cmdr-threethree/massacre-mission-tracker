# Implementation Plan — Elite Dangerous Massacre Mission Tracker

Source of truth: [`CLEAN_ROOM_SPEC.md`](CLEAN_ROOM_SPEC.md).

This plan is derived only from that specification and public Elite Dangerous
journal conventions. It is a clean-room implementation: no existing massacre
mission tracker may be inspected, copied, or translated.

---

## 0. Ground rules and technology choices

- **Python:** 3.11+ (use timezone-aware `datetime`, `StrEnum`, `dataclasses`,
  `tomllib` if a config file is ever added).
- **Runtime dependencies:** `textual` only. Use `argparse` from the standard
  library for the CLI to avoid unnecessary dependencies.
- **Development dependencies:** `pytest`, `pytest-asyncio` only if Textual UI
  tests need it.
- **Packaging:** PEP 621 `pyproject.toml`, `src/` layout, console script
  `massacre-tracker`.
- **State:** strictly in memory. No database, JSON state, cache, log file, or
  clipboard file is written. Read-only CLI/environment configuration is
  allowed.
- **Clean-room:** do not open, install, or consult another tracker while
  implementing. Use the spec and public journal documentation only.
- **Development style:** test-driven for domain rules and ingestion; every
  acceptance test in §16 of the spec becomes an automated test.

### Cross-cutting decisions to record in the README/docstrings

These are places where the spec leaves an implementation choice. Decide once,
write it down, and test it.

1. Ready-to-turn-in compares **station name only**, case-insensitively, as the
   spec requires. Do not silently add system comparison.
2. Unknown `Expiry` is stored as `None` and displayed as `—`; it sorts last.
3. Text grouping is case-insensitive; the original spelling from the oldest
   mission/event is used for display.
4. Reward averages are rounded half-up to whole credits.
5. An unknown commander key is deterministic:
   `__unknown__:<journal filename>`. If a real `Commander` event appears later,
   buffered state migrates to the real FID.
6. Legacy sessions remain visible in diagnostics but never create missions or
   affect kill counts.
7. Clipboard actions are in-memory only. They may show a notification, but must
   not persist anything. System-clipboard integration is optional and not
   required.
8. A full manual rescan is optional; a safe "poll now / rediscover files" action
   is the baseline. If full rebuild is implemented, require confirmation and
   document that manual edits are discarded.

---

## 1. Target architecture

```text
CLI / environment
        │
        ▼
   Config ──────────────► TrackerApp (Textual)
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
        ScanWorker      TailerThread      UI actions
              │               │                │
              └───────────────┴────────────────┘
                              ▼
                     TrackerEngine
                    ┌─────────┴─────────┐
                    ▼                   ▼
             SessionRegistry     CommanderTracker
             (per journal file)  (per FID, domain state)
                    │                   │
                    └──── EventEnvelope ┘
                         (raw event + session context)
```

### Key architectural rules

- **One event pipeline.** Event application is centralized in `TrackerEngine`.
  The scanner and tailer only discover, read, split, parse, sort, and submit
  events.
- **Context is captured at ingestion time.** Every event envelope carries the
  session, filename, line number, live/legacy status, and a snapshot of the
  session's station at that point. This prevents a later `Docked`/`Undocked`
  event from changing the source station of an earlier `MissionAccepted`,
  especially while events are buffered before commander identification.
- **State is owned by CommanderTracker, not by files.** Sessions only hold file
  I/O state, identity, station context, and pending events.
- **One background worker mutates state.** The scanner applies its ordered
  batch, then the tailer owns the polling loop. UI actions take the same engine
  lock. The UI reads immutable snapshots; it does not mutate domain objects
  directly.
- **UI first.** The Textual app mounts before scanning starts. Scanning and
  tailing run in worker threads; progress and completion arrive as messages.
- **Never double-apply.** Offsets are advanced only after a complete line has
  been consumed. A per-session hash set of already-applied relevant lines
  survives truncation so reprocessing does not double-credit bounties.

---

## 2. Proposed project layout

```text
pyproject.toml
README.md
LICENSE
src/massacre_tracker/
    __init__.py
    __main__.py              # python -m massacre_tracker
    cli.py                   # argparse, env fallback, startup errors
    config.py                # Config dataclass and validation
    engine.py                # TrackerEngine, sessions, commander routing
    journal/
        __init__.py
        events.py            # event names, EventEnvelope, timestamp parsing
        discovery.py         # filename timestamp, 8-day window, file stat
        parser.py            # fast relevant-event prefilter + JSON parsing
        reader.py            # byte offsets, partial lines, truncation
        scanner.py           # startup reconstruction and ordering
        tailer.py            # poll_once(), new files, batches
    domain/
        __init__.py
        models.py            # Mission, MissionState, Bounty, station values
        eligibility.py       # MissionAccepted predicate
        lifecycle.py         # accepted/redirected/completed/abandoned/failed/reconcile
        kills.py             # bounty filter, automatic credit, manual adjustments
        summaries.py         # stack, target, ready-state calculations
        tracker.py           # CommanderTracker aggregate
    ui/
        __init__.py
        app.py               # Textual App, workers, keybindings, messages
        screens.py           # help, clipboard, sessions, error/empty states
        widgets.py           # header, commander selector, mission table, panels
        formatting.py        # credits, local expiry, ship names, ages
        app.tcss             # responsive layout at 100x30 and 80x24
tests/
    conftest.py
    builders.py              # synthetic journal line/file builders, FakeClock
    test_discovery.py
    test_reader.py
    test_parser.py
    test_eligibility.py
    test_lifecycle.py
    test_kills.py
    test_summaries.py
    test_engine_sessions.py
    test_scanner.py
    test_tailer.py
    test_acceptance.py       # spec §16, numbered one-to-one
    test_cli.py
    test_ui_smoke.py
```

---

## 3. Phase-by-phase implementation steps

### Phase 1 — Project scaffolding and configuration

**Goal:** an installable package whose CLI validates arguments but does not yet
start a UI.

1. Create `pyproject.toml`:
   - `requires-python = ">=3.11"`.
   - Runtime dependency `textual`.
   - Dev extras: `pytest`.
   - Console script entry point: `massacre-tracker = massacre_tracker.cli:main`.
   - Configure pytest for `tests/` and `src` import path.
2. Create the package skeleton, `__main__.py`, and version constant.
3. Implement `Config`:
   - `journal_dir: Path`
   - `days: int = 8`
   - `poll_interval: float = 1.0`
   - `commander: str | None = None`
   - `color: bool = True`
4. Implement `cli.py` with:
   - `--journal-dir PATH` (fallback env `ED_JOURNAL_DIR`).
   - `--days N` (positive).
   - `--poll SECONDS` (positive).
   - `--commander FID_OR_NAME`.
   - `--no-color`.
   - A clear stderr error for missing/invalid arguments before entering the TUI.
5. Decide and document what `--commander` means: it selects the initial view
   and filters visible missions, but all sessions continue to be tracked. If no
   match exists, start on the first commander and show a warning.
6. Add tests for defaults, env fallback, validation, and bad paths.

**Gate:** `pip install -e .[dev]` succeeds; `massacre-tracker --help` works;
config tests pass.

---

### Phase 2 — Domain model and invariants

**Goal:** a pure, framework-free mission/commander model.

1. In `domain/models.py` define:
   - `MissionState` enum: `ACTIVE`, `REDIRECTED`, `COMPLETED`, `ABANDONED`,
     `FAILED`.
   - `Mission` dataclass with every field from spec §9:
     `mission_id`, `source_system`, `system_address`, `source_station`,
     `issuing_faction`, `destination_system`, `target_faction`,
     `localised_name`, `reward`, `contract_kills`, `progress_kills`, `wing`,
     `accepted_at`, `expires_at`, `state`.
   - `Bounty` dataclass: timestamp, ship type, victim faction, reward, and
     `delta_since_previous` (computed at insertion).
   - A small `StationContext` value object: system, system address, station.
2. Centralize invariants in methods/properties:
   - `progress_kills` is always clamped to `0..contract_kills`.
   - `is_terminal` for completed/abandoned/failed.
   - `is_stack_member` only for `ACTIVE`.
   - `is_target_summary_member` for `ACTIVE` or `REDIRECTED`.
   - `is_ready_to_turn_in(station_names)` is derived, never stored.
3. In `domain/tracker.py` define `CommanderTracker`:
   - `missions: dict[int, Mission]` keyed by mission ID.
   - `bounty_feed: deque[Bounty]` with `maxlen=20`.
   - `add_or_get_mission`, `mark_*`, `reconcile`, `remove_mission`,
     `clear_completed`, `record_bounty`.
4. Define `StackKey = tuple[casefolded_issuer, casefolded_target]` and helpers
   for grouping without losing display casing.
5. Unit tests:
   - State enum/terminal behavior.
   - Clamping.
   - Mission ID uniqueness.
   - Bounty feed keeps the newest 20 and computes deltas against the previous
     retained/inserted bounty.
   - Ready state is recomputed, not cached.

**Gate:** domain model tests pass with no journal or UI imports.

---

### Phase 3 — Domain rules: eligibility, lifecycle, kills, summaries

**Goal:** every rule in specs §8–§12 implemented and tested in isolation.

#### 3.1 Mission eligibility (`domain/eligibility.py`)

Implement a single predicate used by `MissionAccepted`:

1. Session is live (major version >= 4 or unknown treated as live with warning).
2. `context.station` is present.
3. `KillCount` exists and is `> 0`.
4. `TargetFaction` is present and non-empty.
5. `TargetType` contains `MissionUtil_FactionTag_Pirate`, case-insensitive.

Do not filter on `LocalisedName`, reward, or `Wing`. Duplicate mission IDs are
idempotent.

Tests: eligible mission; each ineligible condition; duplicate accepted event;
source station comes from context, not the event.

#### 3.2 Lifecycle (`domain/lifecycle.py`)

1. `MissionAccepted`: create `ACTIVE` only if eligible.
2. `MissionRedirected`: `REDIRECTED`, `progress = contract`; idempotent.
3. `MissionCompleted`: `COMPLETED`; replace reward with event reward.
4. `MissionAbandoned`: `ABANDONED`; reward `0`.
5. `MissionFailed`: `FAILED`; reward `0`.
6. `Missions` reconciliation:
   - Never creates missions.
   - Any tracked ID not in `Active ∪ Complete ∪ Failed` becomes `COMPLETED`.
   - Follow the spec literally, including terminal missions if they are not
     listed.
7. Manual state changes apply the requested state. For consistency, manually
   marking abandoned/failed also zeroes reward; document this.

Tests: every transition, idempotency, reward replacement, reconciliation, no
creation, duplicate suppression.

#### 3.3 Kill accounting (`domain/kills.py`)

1. Bounty eligibility filter, exactly and case-sensitively:
   - `VictimFaction` contains `faction_none`.
   - `VictimFaction` contains `faction_Pirate`.
   - `Target` contains `suit`.
   - `TotalReward <= 0`.
2. Automatic credit:
   - Group eligible `ACTIVE` missions into stacks by
     `(issuing_faction.casefold(), target_faction.casefold())`.
   - Only stacks whose target matches `VictimFaction` case-insensitively.
   - In each matching stack, credit the oldest mission with
     `progress < contract`.
   - Increment exactly one mission per stack.
   - Set `REDIRECTED` immediately when progress reaches contract.
   - A full/redirected mission never blocks a later mission in the same stack.
3. Manual selected-mission adjustment:
   - Clamp to `0..contract`.
   - Reaching contract sets `REDIRECTED`.
   - A redirected mission dropping below contract becomes `ACTIVE`.
4. Manual all-stack `+1`:
   - Sort all non-terminal missions by `accepted_at` ascending.
   - Consider each `(issuer, target)` pair once.
   - Increment the first mission below its contract.
   - Redirect when it reaches contract.
5. Manual all-stack `-1`:
   - Sort by `accepted_at` descending.
   - Consider each pair once.
   - Decrement the first mission with progress > 0.
   - If a `REDIRECTED` mission falls below contract, return it to `ACTIVE`.
6. Bounty feed records only eligible bounties, whether or not they match a
   stack.

Implementation detail: deterministic tie-breaking by `(accepted_at,
mission_id)` so tests and UI are stable.

Tests: spec acceptance 5–8, 17; one-per-stack; multiple stacks; full-mission
non-blocking; manual +/- including redirect/re-activation; clamping.

#### 3.4 Derived summaries (`domain/summaries.py`)

1. `StackSummary`: `ACTIVE` only; group case-insensitively; count, contract
   sum, reward sum, oldest-first list.
2. Deficit per target:
   `max(stack.contract for stacks with same target) - stack.contract`.
3. `TargetSummary`: include `ACTIVE` and `REDIRECTED`; group by issuer
   (case-insensitive):
   - `per_issuer_contract`, `per_issuer_remaining`.
   - `max_stack_contract`, `remaining_required`, `total_contract`.
   - `overlap_ratio` (0 when denominator 0).
   - `kills_to_next_completion`: oldest `ACTIVE` mission per issuer; minimum
     `contract - progress`; `0` if none active.
   - `total_reward`, `shareable_reward` (wing), `turn_in_reward`
     (`REDIRECTED`), `shareable_turn_in_reward`, `mission_count`,
     `average_per_unique_kill`, `average_per_mission`.
   - Active mission count for the UI.
4. Ready-to-turn-in: mission is `REDIRECTED` and any session for the same
   commander has a station whose name equals `source_station`
   case-insensitively.
5. Use half-up rounding for averages; keep exact integers where possible.

Tests: spec acceptance 19, stack deficit, merged faction casing, zero
denominators, unknown expiry sorting, ready-state changes when a session docks
or undocks.

**Gate:** all domain tests pass; no I/O involved.

---

### Phase 4 — Journal discovery, parsing, and byte reader

**Goal:** reliable, fast, offset-correct access to journal files.

#### 4.1 Discovery (`journal/discovery.py`)

1. Normalize the configured directory (`expanduser`, `resolve`).
2. Glob only `Journal.*.log`; never `Status.json`, `Market.json`, etc.
3. Parse the filename timestamp `Journal.YYYY-MM-DDTHHMMSS.NN.log` as UTC.
   Invalid filename timestamps fall back to mtime and produce a warning.
4. Implement `is_in_window(stat, filename_dt, cutoff)`:
   - filename timestamp >= cutoff, **or**
   - mtime >= cutoff.
5. Return a `DiscoveredFile` with path, size, mtime, and filename timestamp.
6. Handle missing directory and unreadable files without raising into the UI.

Tests: spec acceptance 1–2; invalid filename; non-journal files ignored; cutoff
boundary; fake clock injection.

#### 4.2 Fast parser (`journal/parser.py`)

1. Relevant event set:
   `Fileheader`, `Commander`, `Location`, `Docked`, `Undocked`,
   `MissionAccepted`, `MissionRedirected`, `MissionCompleted`,
   `MissionAbandoned`, `MissionFailed`, `Missions`, `Bounty`.
2. Avoid full JSON parsing for unrelated lines:
   - Decode the line, use a compiled bytes/string regex or fast substring check
     for `"event":"<name>"`.
   - Only call `json.loads` when the event name is relevant.
   - If a suspicious non-empty line cannot be parsed, increment the malformed
     counter; blank lines are ignored.
3. Parse timestamps as timezone-aware UTC. Reject relevant events with missing
   or invalid timestamps, counting them as malformed.
4. Build `EventEnvelope`:
   - event name, raw dict, timestamp, filename, line number, session path;
   - `live` status and station snapshot from the reading context.
5. Add a benchmark test/fixture to prove the prefilter is substantially faster
   than parsing every line.

Tests: valid/irrelevant/malformed lines, timestamp parsing, event-name
extraction, UTF-8 errors, blank lines.

#### 4.3 Reader (`journal/reader.py`)

1. Track per-file `offset`, `partial: bytes`, and `line_number`.
2. `read_until(path, offset, start_size)`:
   - Open in binary mode; seek to offset; read in chunks until `start_size`.
   - Never read past the size observed at the start of the scan pass.
   - Decode complete newline-terminated lines only.
   - Strip `\r`; carry the trailing partial line forward.
   - Advance offsets by bytes actually consumed.
3. `read_new(path, state)` for tailing:
   - If `size < offset`, treat as truncated: reset offset/partial/line number,
     keep commander state and the per-session applied-line hash set, then
     reprocess from 0.
   - If `size == offset`, do nothing.
   - Otherwise read to EOF, prepend the partial buffer, split complete lines,
     save the new partial.
4. Never decode/parse a partial line.
5. Count unreadable files and continue.

Tests: spec acceptance 15–16; multibyte UTF-8 split across a read; CRLF; offset
accuracy; no duplicate bytes after two appends.

**Gate:** reader tests pass and exact offsets round-trip.

---

### Phase 5 — Engine, sessions, and commander routing

**Goal:** all event application in one place with correct multi-session
behavior.

#### 5.1 Session state (`engine.py`)

For each file keep:

```text
path
offset
partial buffer
line number
commander FID / name (optional)
system / system address / station (optional)
live state: unknown | live | legacy
last event timestamp
pending envelopes until commander is known
applied relevant-line hashes (for truncation safety)
```

#### 5.2 Commander state

`CommanderTracker` per FID, plus deterministic unknown keys for sessions whose
commander is never identified.

#### 5.3 Event routing

1. `Fileheader`:
   - Parse `gameversion` major; `>= 4` is live.
   - `< 4` becomes legacy: still update diagnostics/identity/station, but ignore
     tracking events.
   - Missing header: treat as live at end of replay (warning) or after a
     fallback period for new live files.
2. `Commander`:
   - Assign FID/name to the session.
   - Create or reuse the tracker.
   - Flush pending envelopes in their original `(timestamp, filename,
     line number)` order.
   - If the session previously used an unknown key, migrate that state to the
     FID.
   - If a session identifies a different commander later, switch the session's
     assignment; do not move the old commander's missions.
3. `Location` / `Docked` / `Undocked`: update only this session's station.
   `Location` with `Docked == false` leaves the station unchanged.
4. `MissionAccepted`: use the envelope's station snapshot and live flag; route
   to the session's commander tracker.
5. Other mission events: route by session commander.
6. `Bounty`: route to the session commander; apply filters and credit rules.
7. `Missions`: reconcile the session commander.
8. Unknown/late commander:
   - Buffer gameplay envelopes until `Commander` appears.
   - At end of replay or after the fallback deadline, flush under
     `__unknown__:<filename>`.
   - Migrate on later identification, merging duplicate mission IDs
     deterministically (keep the existing mission, count a warning) and merging
     bounty feeds by timestamp.

#### 5.4 Legacy handling

- Legacy sessions never create missions or credit kills.
- They still count as watched sessions and show identity/station if known.
- The UI has no legacy view.

#### 5.5 Diagnostics counters

Global per-run counters: events applied, malformed lines, unreadable files,
warnings, scan state, last poll, watched file count. These are read by the UI.

Tests: spec acceptance 3–4, 10–12, legacy isolation, unknown-commander fallback
and migration, station snapshot correctness when a mission is buffered across a
`Docked`/`Undocked` change, no cross-commander leakage.

**Gate:** engine tests pass with synthetic envelopes, no filesystem required.

---

### Phase 6 — Startup reconstruction

**Goal:** correct 8-day replay with exact tail offsets and a non-blocking scan.

1. `JournalScanner` receives the discovery list, cutoff, and engine.
2. For included files:
   - Record `start_size = stat().st_size` before reading.
   - Read in chunks up to `start_size`; report progress (files/lines/bytes).
   - Use a per-file scan context to track live status and station as events are
     encountered, so each envelope carries the correct snapshot.
   - Ignore relevant events older than the cutoff.
   - Parse only relevant event types.
   - Save the trailing partial line.
   - Set the session offset to the exact byte position reached.
3. For excluded files:
   - Do not parse.
   - Register the session with `offset = size`, live unknown, no station.
4. Collect all reconstructed envelopes and sort globally by
   `(timestamp, filename, line number)`.
5. Apply them in order through the engine.
6. After applying:
   - Resolve sessions with no `Commander` to deterministic unknown keys.
   - Resolve missing `Fileheader` to live with a warning.
   - Verify every included file's offset equals its observed end position.
7. Emit `ScanProgress` and `ScanFinished` messages to the UI. The scan worker
   must not touch Textual widgets directly.
8. Handle `FileNotFoundError`/permission errors per file without aborting.

Race handling:

- Files created after discovery are picked up by the tailer from offset 0.
- Files that grow during the scan are read only to `start_size`; the tailer
  consumes the remainder.
- Files that disappear during the scan are marked unreadable and skipped.

Tests: spec acceptance 1–2, 18, 20; global ordering across files; duplicate
event suppression; offset exactness; progress callbacks; empty directory.

**Gate:** a synthetic 8-day corpus reconstructs expected state; no event is
applied twice.

---

### Phase 7 — Live tailing

**Goal:** resilient polling of every journal file.

1. Implement `DirectoryTailer.poll_once(now)` as a deterministic, testable
   function (the worker loop simply calls it with the configured interval).
2. Each poll:
   - Re-discover `Journal.*.log`.
   - Register new files at offset 0 with a fresh session and unknown liveness.
   - For every known file, read appended bytes, prepend partials, split lines,
     save the trailing partial, advance offsets.
   - Detect truncation/shrink and reset per §6.4.
   - Parse relevant events into envelopes, each carrying the current session
     context.
   - Deduplicate against the session's applied-line hashes.
   - Sort the batch by `(timestamp, filename, line number)`.
   - Apply the batch under the engine lock.
   - Update `last_poll`, counters, and session last-event times.
   - Emit a UI refresh message if anything changed.
3. New files:
   - Start at 0.
   - Buffer gameplay events until `Fileheader`/`Commander` is seen.
   - If no `Fileheader` after a reasonable fallback (e.g., 10 seconds), treat
     as live with a warning.
4. Deleted files:
   - Mark the session inactive/unreadable; do not discard commander missions.
   - If it reappears, resume from the stored offset or truncation rules.
5. Errors:
   - Malformed JSON: skip, count, continue.
   - Unreadable file: report, keep watching others.
   - Never let a single file stop the poll loop.
6. Poll interval:
   - Use `Config.poll_interval`; default 1.0 seconds, minimum enforced > 0.
   - Sleep interruptibly so quitting is immediate.
7. Thread lifecycle:
   - Start only after reconstruction completes.
   - Stop cleanly on app exit.
   - Never write state on exit.

Tests: spec acceptance 13–17; two polls with no data; appended partial line;
truncation; new file; malformed line; unreadable file; ordering within a batch;
poll interval not required in unit tests.

**Gate:** all ingestion tests pass using `poll_once`, with no `sleep()`.

---

### Phase 8 — Terminal UI (Textual)

**Goal:** responsive, usable UI at 100×30 and graceful at 80×24.

#### 8.1 App shell

1. `TrackerApp(Config, Engine)`.
2. Mount immediately with header, selector, main table, details, footer.
3. Start the scanner worker in `on_mount`; show "Scanning…" status.
4. On scan completion, start the tailer worker and refresh.
5. Use a message (`EngineUpdated`) from workers; never call widget methods from
   a background thread directly.
6. Use engine snapshots for rendering, taken under the lock and converted to
   plain data.

#### 8.2 Header/status (`widgets.py`)

Show:

- Journal directory.
- Watched file count.
- Active sessions and commanders.
- Scan status/progress.
- Last poll time.
- Events processed this run.
- Malformed-line count / error indicator.
- Legacy/unknown warnings compactly.

#### 8.3 Commander/session selector

- Default: one commander per view.
- `Select` or a compact list with commander name + FID.
- A `Sessions` screen lists every session: commander/FID, system/station,
  filename, last event age, active/idle, live/legacy.
- Sessions sharing a FID are grouped under one commander.
- No combined cross-commander mission table by default.
- `--commander` sets the initial selection.

#### 8.4 Main mission table

Columns exactly as required:

```text
SYSTEM | STATION | ISSUING FACTION | TARGET FACTION |
KILLS | REWARD | EXPIRES | WING | STATE
```

- `KILLS` → `progress / contract`.
- `REWARD` → thousands separators + `cr`.
- `EXPIRES` → local time; unknown as `—`.
- Wing marked visually (badge/text).
- Ready-to-turn-in rows highlighted.
- State visible as column/badge/color with a legend.
- Active view: `ACTIVE` + `REDIRECTED`.
- Completed view: `COMPLETED` + `ABANDONED` + `FAILED`.
- Empty state with a short explanation.
- Default active sort: system, station, issuing faction, expires_at ascending.

#### 8.5 Detail panels

Use a bottom tabbed area to preserve vertical space:

- **Stacks:** issuer, target, mission count, contract kills, deficit, reward.
- **Targets:** every field from spec §13.8, including active count, overlap,
  remaining, kills-to-next, rewards, averages.
- **Bounties:** newest first, last 20, ship/victim/reward/time since previous.
- **Sessions:** per-session diagnostics.
- Optional **Clipboard:** in-memory entries.

At 100×30 the table and one panel tab should be usable; at 80×24 reduce header
verbosity, shrink columns, and let the detail area collapse or scroll. Test the
layout at both sizes with Textual's test runner.

#### 8.6 Sorting

Provide sort keys: system, station, issuer, target, contract kills, reward,
expiry, wing. Toggle direction. Keep sort state per view/commander in memory.

- Text keys compare case-insensitively.
- Expiry `None` sorts last in both directions or consistently as documented.
- Stable tie-break by mission ID.

#### 8.7 Actions and keybindings

Implement and document:

| Action | Suggested key |
|---|---|
| Increment selected mission | `+` |
| Decrement selected mission | `-` |
| Increment all stacks | `]` |
| Decrement all stacks | `[` |
| Mark completed | `c` |
| Mark abandoned | `a` |
| Mark failed | `f` |
| Mark active again | `r` |
| Delete selected mission | `delete` |
| Add source to clipboard | `y` |
| Copy source system/station/destination | submenu on `Y` |
| Clear completed entries | `ctrl+x` |
| Switch commander | `ctrl+n` / selector |
| Switch Active/Completed | `v` |
| Change sort key | `s` |
| Toggle sort direction | `S` |
| Manual rescan/poll now | `R` |
| Help | `?` |
| Quit | `q` |

- Footer shows the most important bindings; `?` opens a full help screen.
- Confirm destructive actions (delete, clear completed, full rescan).
- Actions are no-ops with a notification when no mission is selected or the
  mission is terminal where inappropriate.
- Manual changes are in memory only and are lost on restart (documented).

#### 8.8 Clipboard

- In-memory list of `(destination system, source system, source station)`
  triples; deduplicate identical triples.
- Add via `y`; copy individual fields via submenu.
- Show entries in a modal/panel; copy action shows a notification.
- Nothing is written to disk; cleared on exit.
- If system clipboard integration is added, it is optional and must not write
  tracker state.

#### 8.9 Error and empty states

- Missing/unreadable journal directory: dedicated error screen showing the
  configured path, with retry and quit.
- No journal files: empty state; polling continues.
- No missions: empty table explanation.
- Scanning: progress and quit still available.
- Malformed lines: diagnostic count, never a crash.

#### 8.10 UI tests

- Textual `run_test` smoke tests: app mounts; empty state; missing-directory
  error; mission table populated; commander switch; view switch; sort toggle;
  selected mission increment triggers a domain change; help screen opens.
- Snapshot/row-count assertions rather than brittle full-screen snapshots.

**Gate:** UI renders under 0.5 s in a smoke test; both target sizes usable; all
actions mutate in-memory state correctly.

---

### Phase 9 — Diagnostics, polish, and robustness

1. Surface scan/tail errors without modal spam; keep a warnings list.
2. Ensure UI refresh is batched: one refresh per poll, not per event.
3. Handle rapid file creation, deletion, and permission changes.
4. Handle sessions with no commander, no header, no station.
5. Handle duplicate `MissionID`, duplicate `MissionRedirected`, duplicate
   `MissionCompleted`, repeated `Missions` snapshots.
6. Verify no writes:
   - Search the code for `open(..., "w")`, `write_text`, `write_bytes`, temp
     files, sqlite, logging-to-file.
   - Add a test that snapshots the journal directory tree before/after a full
     run and asserts it is unchanged.
7. Add graceful shutdown:
   - Stop the tailer.
   - Join the worker with a short timeout.
   - Write nothing.
8. Optional: full manual rescan with confirmation:
   - Stop tailer, rebuild engine from a fresh scan, restart tailer.
   - Clear manual edits/clipboard with user consent.
9. Optional: combined commander view, explicitly opt-in only.

---

### Phase 10 — Acceptance, performance, and documentation

#### 10.1 Acceptance test matrix

Implement each spec §16 test as a named pytest test in `tests/test_acceptance.py`
(or split across focused files while keeping the numbering in test names).

| # | Test | Primary layer | Phase |
|---|---|---|---|
| 1 | Eight-day window | discovery/scanner | 4, 6 |
| 2 | Long-running session | discovery/scanner | 4, 6 |
| 3 | Eligible mission | eligibility/engine | 3, 5 |
| 4 | Ineligible mission | eligibility/engine | 3, 5 |
| 5 | Kill attribution within a stack | kills | 3 |
| 6 | Kill attribution across stacks | kills | 3 |
| 7 | Automatic ready state | kills | 3 |
| 8 | Redirect event | lifecycle | 3 |
| 9 | Completion and failure | lifecycle | 3 |
| 10 | Reconciliation | lifecycle | 3 |
| 11 | Different commanders | engine | 5 |
| 12 | Same commander, multiple files | engine | 5 |
| 13 | New file after startup | tailer | 7 |
| 14 | Append to an old file | tailer | 7 |
| 15 | Partial line | reader/tailer | 4, 7 |
| 16 | Truncation | reader/tailer | 4, 7 |
| 17 | Bounty filters | kills | 3 |
| 18 | Stateless restart | scanner/engine | 6 |
| 19 | Summary calculations | summaries | 3 |
| 20 | No writes | integration | 9, 10 |

#### 10.2 Synthetic fixtures

Build helpers in `tests/builders.py`:

- `event(name, timestamp, **fields)` → compact JSON line.
- `journal_file(path, events, append=False)`.
- `set_mtime(path, dt)` and fake filename timestamps.
- `FakeClock` injecting `now` into discovery/scanner/tailer.
- `run_scan(files, now, config)` and `run_poll(tailer)` helpers.
- Fixture journals covering live, legacy, duplicate IDs, all mission events,
  bounty variants, multiple commanders, and concurrent files.

Never depend on a live game or the user's real journal directory.

#### 10.3 Performance checks

1. Add `scripts/bench_scan.py` (or a pytest marker) generating a large
   synthetic corpus (e.g., 500k lines, 8 days) with mostly irrelevant events.
2. Measure:
   - Main UI first render < 0.5 s.
   - Typical scan 1–3 s; target < 6 s on slower storage.
   - Live event to UI < 2 s at 1 s polling.
   - Memory < 200 MB for the 8-day window.
3. If parsing is too slow:
   - Strengthen the relevance prefilter.
   - Read in large binary chunks.
   - Avoid creating datetime objects for irrelevant events.
   - Avoid retaining raw irrelevant lines.
4. Keep only relevant envelopes during reconstruction; do not hold entire file
   contents in memory.

#### 10.4 README

Document:

- What the app does and does not do (live-only, 8-day window, in-memory,
  no Legacy view, no mission-board scraping, no network).
- Installation (`pip install -e .`), Python version, Textual.
- Usage and all CLI options/environment variable.
- Journal directory location examples for Windows/Linux (explicit path
  required; auto-detection optional).
- Keybindings and UI tour.
- Mission eligibility, bounty credit, stack/target summary semantics, and
  ready-to-turn-in definition (station name only).
- Stateless behavior: restart reconstructs; manual edits/clipboard are lost;
  nothing is written.
- Troubleshooting: missing directory, no files, malformed lines, unknown
  commander warnings, legacy sessions.
- Limitations and deliberate interpretations (unknown expiry, rounding,
  Unknown commander key, optional rescan).
- Clean-room statement.

#### 10.5 Final definition of done

- [ ] `pytest` passes, including all 20 acceptance tests.
- [ ] `massacre-tracker --journal-dir <dir>` starts immediately and scans in
      the background.
- [ ] Live appends in any known file and brand-new files are processed without
      restart.
- [ ] Multiple commanders are isolated; multiple files for one commander share
      mission state but not station context.
- [ ] Legacy sessions are ignored for tracking with no Legacy view.
- [ ] Partial lines, truncation, malformed JSON, missing files, and permission
      errors are handled without crashing.
- [ ] All eight required mission-table columns, stack/target/bounty panels, and
      selectors are present.
- [ ] Required actions and discoverable keybindings are implemented.
- [ ] No state or clipboard file is created; no game state is written on exit.
- [ ] UI is usable at 100×30 and degrades gracefully at 80×24.
- [ ] README covers setup, usage, configuration, interpretation decisions, and
      limitations.

---

## 4. Suggested milestone order (commit-sized)

1. **M1 — Skeleton:** packaging, config, CLI, test harness.
2. **M2 — Domain:** models, eligibility, lifecycle, kills, summaries, tests.
3. **M3 — Ingestion primitives:** discovery, parser, reader, tests.
4. **M4 — Replay:** engine routing, scanner, 8-day reconstruction tests.
5. **M5 — Live:** tailer, new files, truncation, partial lines, tests.
6. **M6 — Multi-session:** commander isolation, shared FID, unknown fallback,
   station snapshots.
7. **M7 — UI:** shell, table, selector, panels, actions, clipboard, error states.
8. **M8 — Acceptance:** all 20 tests, no-write test, performance checks.
9. **M9 — Docs/polish:** README, help screen, layout checks, cleanup.

---

## 5. Highest-risk areas and mitigations

| Risk | Mitigation |
|---|---|
| Events applied before commander identity is known | Buffer per session; carry station/live snapshots in each envelope; flush at `Commander` or deterministic unknown fallback. |
| Station changes corrupt mission source | Capture station snapshot at ingestion, never read live session station when applying a buffered `MissionAccepted`. |
| Multi-session state leakage | Key all mission/bounty state by FID; keep station, offset, partial, and liveness per file. |
| Double-credit after truncation/reprocess | Advance offsets only after complete lines; keep per-session applied-line hashes across truncation. |
| Global ordering bugs | Include `(timestamp, filename, line number)` in every envelope; sort before applying each batch. |
| UI stall during scan | Mount UI first; scan in a worker; apply progress messages; never parse in the UI thread. |
| Textual thread misuse | Mutate domain under a lock; send messages to the UI; UI renders snapshots. |
| Malformed or partial JSON crashes | Newline-gated line splitting; per-line try/except; malformed counter. |
| Performance on large journals | Relevance prefilter, chunked binary reads, only relevant envelopes retained, benchmark corpus. |
| Accidental persistence | No write helpers in code; integration test snapshots the journal directory; review for `open(..., "w")`. |
| Legacy leakage | Single liveness gate in the eligibility/engine path; explicit legacy tests. |
| Ambiguous spec details | Record decisions in README and keep them covered by tests. |

---

## 6. Verification commands for the finished project

```bash
python -m pytest
python -m pytest tests/test_acceptance.py -v
python -m pytest -k "not ui" -q
massacre-tracker --help
massacre-tracker --journal-dir /path/to/journals --days 8 --poll 1.0
```

Optional manual smoke test:

1. Start with an empty temp journal directory.
2. Confirm the empty state and that polling continues.
3. Append a live `Fileheader`, `Commander`, `Docked`, and eligible
   `MissionAccepted` event.
4. Confirm the mission appears without restart.
5. Append a matching `Bounty`; confirm progress increments and the mission
   becomes redirected at contract.
6. Dock at the source station; confirm the ready highlight.
7. Quit; confirm no new files were created anywhere.
