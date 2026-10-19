# Elite Dangerous Massacre Mission Tracker

## Clean-Room Implementation Specification

**Audience:** an independent implementation agent.

**Allowed inputs:** this specification and public Elite Dangerous journal documentation. Do not consult, copy, or translate any existing implementation of a massacre mission tracker. Do not copy code, comments, identifiers, UI text, or assets from another project.

**Deliverable:** a stateless, live-only Python terminal application that reconstructs recent mission state from journal files and then tracks new events in real time.

---

## 1. Product summary

The application tracks massacre missions accepted by Elite Dangerous commanders.

It must:

- Read Elite Dangerous journal files.
- Track only Live game sessions.
- Reconstruct the last 8 days of relevant events on startup.
- Continue reading new events from every journal file in the directory, including:
  - New lines appended to existing files.
  - Entirely new journal files.
- Support multiple simultaneously active journal sessions and multiple commanders.
- Keep all mission, bounty, and session state in memory only.
- Write no game state to disk.
- Present the data in a responsive terminal UI.

The application is not a mission-board scraper, route planner, trade tool, or historical archive. It is a live mission tracker.

---

## 2. Definitions

| Term | Meaning |
|---|---|
| **Session** | One journal file being read. A game launch normally creates one file. |
| **Commander** | A player account identified by `FID`. |
| **Mission** | A tracked massacre mission. |
| **Contract kills** | The `KillCount` required by the mission. |
| **Progress kills** | The number of kills credited so far. |
| **Stack** | All active missions from one issuing faction against one target faction. |
| **Target summary** | Aggregated information for one victim/target faction. |
| **Ready to turn in** | A mission whose kill requirement is met and which can be handed in. |
| **Live** | A session whose journal `gameversion` has major version 4 or greater. |

---

## 3. Runtime behaviour

### 3.1 Startup

1. Determine the journal directory.
2. Discover journal files.
3. Reconstruct mission state from events in files touched within the last 8 days.
4. Render the main UI.
5. Start tailing every discovered journal file.
6. Process new events as they arrive.

The UI should render immediately. The 8-day reconstruction may run in a background worker and report progress.

### 3.2 Stateless operation

- Do not persist missions, bounties, stacks, sessions, completed missions, clipboard entries, or UI selections.
- Do not create a database.
- Do not write JSON mission files.
- On exit, write nothing related to game state.
- On restart, reconstruct state again from the 8-day journal window.
- Read-only configuration from CLI, environment variables, or an optional config file is permitted. The implementation must not write game state.

### 3.3 Multiple sessions

- A journal file is a session.
- Session-specific state includes:
  - File path and read offset.
  - Partial line buffer.
  - Commander identity once known.
  - Current station.
  - Live/ignored status.
  - Last event time.
- Mission and bounty state belongs to the commander, not to the file.
- Multiple files belonging to the same commander share the same mission tracker.
- Different commanders are fully isolated.
- Never let one session's current station affect another session's mission acceptance.

### 3.4 No Legacy support

- A session is live if its `Fileheader` event has `gameversion` major version 4 or greater.
- Ignore tracking events from Legacy sessions.
- Do not display a Legacy view.
- If a file has no `Fileheader`, treat it as live for best-effort tracking and report a warning.

---

## 4. Journal input contract

### 4.1 File format

- Journal files are UTF-8, newline-delimited JSON.
- Each line is one event object.
- Timestamps are UTC ISO-8601 strings.
- Files may be appended while being read.
- Multiple files may be appended at the same time.
- A file may be created while the application is running.
- A line may be incomplete when read; wait for the newline before parsing it.

Journal files match:

```text
Journal.*.log
```

Do not treat `Status.json`, `Market.json`, `NavRoute.json`, or other files as journal input.

### 4.2 Events used

The implementation only needs events relevant to station state, mission lifecycle, commanders, game version, and bounties.

#### `Fileheader`

Used fields:

```text
event, timestamp, gameversion
```

`gameversion` is a version string. Major version `>= 4` means Live.

#### `Commander`

Used fields:

```text
event, timestamp, Name, FID
```

`FID` is the commander's unique identifier. Use it as the tracker key.

#### `Location`

Used fields:

```text
event, timestamp, Docked, StarSystem, SystemAddress, StationName
```

If `Docked` is true, this becomes the session's current station. If false, it does not change the current station.

#### `Docked`

Used fields:

```text
event, timestamp, StarSystem, SystemAddress, StationName
```

Sets the session's current station.

#### `Undocked`

Used fields:

```text
event, timestamp
```

Clears the session's current station.

#### `MissionAccepted`

Used fields:

```text
event, timestamp, MissionID, Faction, DestinationSystem, TargetFaction,
LocalisedName, Reward, KillCount, Wing, Expiry, TargetType
```

- `Faction` is the issuing faction.
- Source system/station are taken from the session's current station, not the event.
- `Expiry` may be absent.

#### `MissionRedirected`

Used fields:

```text
event, timestamp, MissionID
```

Means the kill requirement has been met and the mission can be turned in.

#### `MissionCompleted`

Used fields:

```text
event, timestamp, MissionID, Reward
```

Completes the mission and replaces its stored reward with the event reward.

#### `MissionAbandoned`

Used fields:

```text
event, timestamp, MissionID
```

#### `MissionFailed`

Used fields:

```text
event, timestamp, MissionID
```

#### `Missions`

Used fields:

```text
event, timestamp, Active, Complete, Failed
```

`Active`, `Complete`, and `Failed` are arrays. Each item contains at least `MissionID`.

This event is a reconciliation snapshot, not a source of mission details.

#### `Bounty`

Used fields:

```text
event, timestamp, Target, VictimFaction, TotalReward
```

- `Target` is a ship type identifier.
- `VictimFaction` is the faction of the destroyed ship.
- `TotalReward` is the bounty value.

Other event types may be ignored.

---

## 5. Startup reconstruction

### 5.1 Time window

Use an 8-day window:

```text
cutoff = current UTC time - 8 days
```

Include a journal file if either:

- The timestamp embedded in its filename is at or after the cutoff, or
- The file's modification time is at or after the cutoff.

This ensures that a long-running session that started more than 8 days ago is still included when it has recent writes.

### 5.2 Parsing

For each included file:

1. Read the file contents up to the size observed when reading begins.
2. Split complete newline-terminated lines.
3. Keep any trailing incomplete line per file for the live tailer.
4. Ignore events older than the cutoff.
5. Parse only relevant event types.
6. Record the exact byte offset reached, for live tailing.

For files outside the window:

- Do not parse existing contents.
- Record the current file size as the starting offset.
- Only process data appended after startup.

### 5.3 Ordering

Collect all reconstructed events and sort them globally by:

```text
(timestamp, filename, line number)
```

Apply them in that order. This keeps events from concurrent sessions reasonably ordered.

### 5.4 Startup performance

- The main UI must appear before or while the scan runs.
- Do not block the UI while scanning.
- Skip unrelated lines without full JSON parsing where possible.
- Target a typical 8-day reconstruction time of 1–3 seconds, and less than 6 seconds on slower storage.
- Show scan progress if the scan takes more than a moment.

### 5.5 Starting live tailing

After reconstruction:

- Set each file's tail offset to the exact end position reached during reconstruction.
- Start the polling loop.
- Any bytes appended after reconstruction are processed by the tailer.
- Events must not be applied twice.

---

## 6. Live tailing

### 6.1 Polling

Poll the journal directory every 1 second by default. The interval may be configurable.

On each poll:

1. Discover `Journal.*.log` files.
2. Register newly created files with offset `0`.
3. For each file with `size > offset`, read the new bytes.
4. Prepend any saved partial line.
5. Split complete lines.
6. Save any trailing incomplete line.
7. Update the offset.
8. Parse relevant events.
9. Sort the batch by `(timestamp, filename, line number)`.
10. Apply the batch.
11. Refresh the UI.

### 6.2 New files

A new file is read from the beginning. Its events are processed normally.

### 6.3 Appended data in old files

All known files are tailed, including files that are not the newest. Do not assume only the latest journal file is active.

### 6.4 Truncation or replacement

If a file's size becomes smaller than the stored offset, treat it as truncated:

- Reset the offset to `0`.
- Clear the partial buffer.
- Reprocess the file.
- Keep the commander tracker unless the file identifies a different commander.

### 6.5 Partial lines

Never parse an incomplete trailing line. Carry the bytes forward until a newline arrives.

### 6.6 Errors

- A malformed JSON line must be skipped without stopping the app.
- Count and display malformed lines in diagnostics.
- An unreadable file must not crash the app; report it and continue watching other files.

---

## 7. Sessions and commanders

### 7.1 Session association

For each file, maintain:

```text
path
offset
partial buffer
commander FID (optional)
commander name (optional)
current station (optional)
live/ignored state
last event timestamp
pending events before commander identity is known
```

### 7.2 Commander association

- On `Commander`, assign the FID and name to the session.
- Create or reuse the commander tracker for that FID.
- If gameplay events arrive before the session's commander is known, buffer them and apply them once the commander is identified.
- If no commander is identified after a reasonable fallback period or end of replay, place the session under a deterministic unknown key derived from the filename. If a commander appears later, migrate the buffered state to the real tracker.

### 7.3 Shared commander state

If multiple sessions share a FID:

- They share missions, stacks, target summaries, and the bounty feed.
- Station context remains per session.
- Mission acceptance uses the accepting session's station.

### 7.4 UI grouping

- Default to one commander per view.
- Provide a session/commander selector.
- Do not mix different commanders in one mission table by default.
- A combined view may be offered only as an explicit option.

---

## 8. Mission eligibility

A `MissionAccepted` event creates a tracked mission only if all are true:

1. The session is live.
2. The session has a known current station.
3. `KillCount` is present and greater than zero.
4. `TargetFaction` is present and non-empty.
5. `TargetType` is present and contains `MissionUtil_FactionTag_Pirate`, compared case-insensitively.

Do not filter by mission name, `LocalisedName`, reward, or wing status.

Missions that fail the predicate are ignored.

Duplicate `MissionID` events must be idempotent: do not create a second mission with the same ID for the same commander.

---

## 9. Mission model

Represent at least:

| Field | Meaning |
|---|---|
| `mission_id` | Unique mission ID |
| `source_system` | System where accepted |
| `system_address` | System address where accepted |
| `source_station` | Station where accepted |
| `issuing_faction` | `Faction` from the event |
| `destination_system` | `DestinationSystem` from the event |
| `target_faction` | Victim faction |
| `localised_name` | Raw `LocalisedName` |
| `reward` | Current reward in credits |
| `contract_kills` | Required kills |
| `progress_kills` | Credited kills |
| `wing` | Whether the mission is a wing mission |
| `accepted_at` | Event timestamp |
| `expires_at` | Event `Expiry`, or a clearly marked unknown value |
| `state` | Lifecycle state |

### 9.1 States

```text
ACTIVE
REDIRECTED
COMPLETED
ABANDONED
FAILED
```

### 9.2 Invariants

- `0 <= progress_kills <= contract_kills`.
- A mission with `state == ACTIVE` participates in its stack.
- A mission with `state == REDIRECTED` does not participate in its stack but does participate in target summaries.
- Completed, abandoned, and failed missions do not participate in active stacks or active target summaries.
- Expiry is informational and must not automatically change state.
- `mission_id` is unique within a commander.

---

## 10. Lifecycle rules

| Event / action | Result |
|---|---|
| `MissionAccepted` | Create as `ACTIVE` if eligible |
| `MissionRedirected` | Set `REDIRECTED`, set `progress_kills = contract_kills`; idempotent |
| `MissionCompleted` | Set `COMPLETED`; replace reward with event reward |
| `MissionAbandoned` | Set `ABANDONED`; reward becomes `0` |
| `MissionFailed` | Set `FAILED`; reward becomes `0` |
| `Missions` reconciliation | Any tracked mission ID not in `Active ∪ Complete ∪ Failed` becomes `COMPLETED` |
| Manual kill adjustment reaches contract | Set `REDIRECTED` |
| Manual state change | Apply the requested state |

`Missions` reconciliation must not create missions. It only updates existing tracked missions.

---

## 11. Kill accounting

Kill progress comes from `Bounty` events.

### 11.1 Bounty eligibility

Ignore a bounty when any of these is true:

```text
VictimFaction contains "faction_none"
VictimFaction contains "faction_Pirate"
Target contains "suit"
TotalReward <= 0
```

Substring checks are case-sensitive.

### 11.2 Credit rule

For every stack belonging to the session's commander whose target faction matches `VictimFaction` case-insensitively:

1. Select the oldest `ACTIVE` mission in that stack with `progress_kills < contract_kills`.
2. Increment its progress by 1.
3. If progress reaches contract kills, set state to `REDIRECTED` immediately.
4. Do not credit more than one mission per stack for a single bounty.

Consequences:

- A single bounty can credit one mission in each matching issuer/target stack.
- It never credits two missions in the same stack.
- A full mission must not block later missions in the same stack.
- A later `MissionRedirected` event for an already-redirected mission must be harmless.

### 11.3 Manual adjustment

Support adjusting one selected mission and adjusting all stacks at once.

For all-stack `+1`:

1. Sort missions by `accepted_at` ascending.
2. Consider each `(issuing_faction, target_faction)` pair once.
3. Increment the first eligible mission that is not at its contract limit.
4. If it reaches the contract limit, set it to `REDIRECTED`.

For all-stack `-1`:

1. Sort missions by `accepted_at` descending.
2. Consider each pair once.
3. Decrement the first mission with progress greater than zero.
4. If a `REDIRECTED` mission drops below its contract limit, set it back to `ACTIVE`.

Clamp all progress changes to `0..contract_kills`.

### 11.4 Bounty feed

Maintain the last 20 eligible bounties per commander:

```text
timestamp
time since previous bounty for that commander
ship type
victim faction
bounty reward
```

Display newest first. The time delta is relative to the previous bounty in that commander's feed.

---

## 12. Derived summaries

### 12.1 Stack summary

Group all `ACTIVE` missions by `(issuing_faction, target_faction)`, case-insensitive.

For each stack:

```text
mission_count = number of missions
contract_kills = sum of contract_kills
reward = sum of reward
oldest_first = missions sorted by accepted_at ascending
```

For each stack, calculate a **deficit**:

```text
largest_contract = max(contract_kills) among stacks with the same target faction
deficit = largest_contract - this_stack.contract_kills
```

A deficit of zero means it is the largest stack for that target faction.

### 12.2 Target-faction summary

Include missions in `ACTIVE` or `REDIRECTED` state. Exclude `COMPLETED`, `ABANDONED`, and `FAILED`.

Group included missions by issuing faction and calculate:

```text
per_issuer_contract = sum(contract_kills) for that issuer
per_issuer_remaining = sum(contract_kills - progress_kills) for that issuer
```

Then:

```text
max_stack_contract = max(per_issuer_contract), or 0
remaining_required = max(per_issuer_remaining), or 0
total_contract = sum(contract_kills across all included missions)
overlap_ratio = total_contract / max_stack_contract, or 0 if max_stack_contract is 0
```

`remaining_required` represents the number of unique kills still needed to satisfy the largest outstanding stack for that target.

`kills_to_next_completion`:

1. For each issuing faction, choose its oldest `ACTIVE` mission.
2. Compute `contract_kills - progress_kills`.
3. Report the minimum across issuers.
4. If no active missions exist, report `0`.

Value summaries:

```text
total_reward = sum(reward) across included missions
shareable_reward = sum(reward) where wing == true
turn_in_reward = sum(reward) where state == REDIRECTED
shareable_turn_in_reward = sum(reward) where state == REDIRECTED and wing == true
mission_count = number of included missions
average_per_unique_kill = total_reward / max_stack_contract  (0 if denominator is 0)
average_per_mission = total_reward / mission_count           (0 if denominator is 0)
```

Display reward averages rounded to the nearest whole credit.

### 12.3 Ready-to-turn-in state

A mission is display-ready when:

- `state == REDIRECTED`, and
- any session belonging to the same commander has a current station whose name matches `source_station` case-insensitively.

This is derived state. Do not store a stale boolean on the mission.

Compare station name only unless the implementation deliberately improves this to station plus system; if so, document the change.

---

## 13. Terminal UI requirements

The implementation may choose its TUI framework. Textual is recommended for Python.

### 13.1 Layout

Provide:

- A header/status area.
- A session or commander selector.
- A main mission table.
- Stack summary panel.
- Target-faction summary panel.
- Bounty feed.
- A footer with keybindings.
- Startup scan progress and live-tail status.

The UI should remain usable at 100×30 and degrade gracefully at 80×24.

### 13.2 Header/status

Show:

- Journal directory.
- Number of journal files currently watched.
- Active sessions and commanders.
- Scan status during startup.
- Last poll time.
- Count of events processed in this run.
- Malformed-line count or an error indicator.

### 13.3 Session selector

For each session show:

- Commander name and FID if known.
- Current station and system if docked.
- Journal filename.
- Last event age.
- Active/idle indicator.

Selecting a commander filters the mission table and summaries. Sessions sharing a commander are grouped.

### 13.4 Main mission table

Columns:

```text
SYSTEM | STATION | ISSUING FACTION | TARGET FACTION |
KILLS | REWARD | EXPIRES | WING | STATE
```

Requirements:

- `KILLS` displays `progress_kills / contract_kills`.
- `REWARD` uses thousands separators and a `cr` suffix.
- `EXPIRES` is displayed in local time.
- Wing missions are visually marked.
- Ready-to-turn-in missions are highlighted.
- Completed/abandoned/failed missions appear in a separate Completed view.
- State is visible either as a column, badge, or colour with a legend.

### 13.5 Views

- **Active**: missions with state `ACTIVE` or `REDIRECTED`.
- **Completed**: missions with state `COMPLETED`, `ABANDONED`, or `FAILED`.

No Legacy view.

### 13.6 Sorting

Provide sorting by:

- System
- Station
- Issuing faction
- Target faction
- Contract kills
- Reward
- Expiry
- Wing

Default active sort:

```text
system, station, issuing faction, expires_at
```

All ascending. Allow toggling direction.

### 13.7 Stack panel

Show:

- Issuing faction
- Target faction
- Mission count
- Contract kills
- Deficit
- Reward

### 13.8 Target-faction panel

Show:

- Target faction
- Mission count
- Active mission count
- Largest stack contract kills
- Total contract kills
- Overlap ratio
- Remaining required kills
- Kills to next completion
- Total reward
- Shareable reward
- Turn-in reward
- Shareable turn-in reward
- Average per unique kill
- Average per mission

### 13.9 Bounty feed

Show the last 20 eligible bounties for the selected commander:

```text
TARGET SHIP | VICTIM FACTION | REWARD | TIME SINCE PREVIOUS
```

Newest first. Localize ship type identifiers for display; a small mapping is sufficient, with a title-case fallback.

### 13.10 Actions and keybindings

Provide actions for:

- Increment/decrement selected mission kills.
- Increment/decrement all stacks.
- Mark selected mission completed/abandoned/failed.
- Mark selected mission active again.
- Delete selected mission from memory.
- Add mission source to an in-memory clipboard.
- Copy source system, source station, or destination system.
- Clear completed entries from memory.
- Switch commander/session.
- Switch Active/Completed view.
- Change sort key/direction.
- Quit.
- Trigger a manual rescan if useful.

Suggested defaults may be changed, but they must be discoverable in the footer/help.

### 13.11 Clipboard

Clipboard entries are in-memory only:

```text
destination system
source system
source station
```

Deduplicate identical triples. On exit, discard them.

### 13.12 Error and empty states

- Missing journal directory: clear error screen with the configured path.
- No journal files: show empty state and continue polling.
- No missions: show an empty table with a short explanation.
- Scan in progress: show progress and allow quitting.
- Malformed journal lines: do not crash; show a diagnostic count.

---

## 14. Configuration

Support at least:

```text
--journal-dir PATH
--days 8
--poll 1.0
--commander FID_OR_NAME
--no-color
```

Environment variable alternative for journal directory is acceptable.

Auto-detection of common journal locations is optional. The app must work with an explicitly supplied directory.

The application must not require a config file and must not write game state.

---

## 15. Non-functional requirements

### 15.1 Performance

- Main UI first render: under 0.5 seconds.
- Typical 8-day scan: 1–3 seconds.
- Slow-storage 8-day scan: under 6 seconds where practical.
- New live event to UI: under 2 seconds with the default poll interval.
- Memory target: under 200 MB for a typical 8-day window.

### 15.2 Robustness

- Handle partial lines.
- Handle new files.
- Handle appends to any existing file.
- Handle file truncation.
- Handle multiple concurrent sessions.
- Handle missing or unreadable files.
- Handle malformed JSON lines.
- Handle missing commander events with a fallback.
- Never double-process an event.
- Never write state on exit.

### 15.3 Time

- Journal timestamps are UTC.
- Display expiry in local time.
- Use timezone-aware datetimes internally.

### 15.4 Testing

Provide automated tests for the domain rules and journal ingestion. Use synthetic journal fixtures; do not depend on a live game.

---

## 16. Acceptance tests

### Test 1 — Eight-day window

Given:

- One journal file with filename timestamp 10 days ago and old `mtime`.
- One journal file within the last 8 days.

Expected:

- Only the recent file is reconstructed.
- Both files are watched for future appends.

### Test 2 — Long-running session

Given a file whose filename timestamp is 9 days old but whose `mtime` is recent.

Expected:

- The file is included in reconstruction.
- Events older than the cutoff are ignored.
- Events within the cutoff are applied.

### Test 3 — Eligible mission

Given a live session docked at a station and a `MissionAccepted` event with positive `KillCount`, non-empty `TargetFaction`, and a pirate target tag.

Expected:

- One mission is created in `ACTIVE` state.
- Source system and station come from the session's current station.

### Test 4 — Ineligible mission

Given any of:

- No current station.
- `KillCount` missing or zero.
- Empty `TargetFaction`.
- Non-pirate `TargetType`.
- Legacy session.

Expected:

- No mission is created.

### Test 5 — Kill attribution within a stack

Given two active missions from the same issuer against the same target.

Expected:

- One bounty credits only the older mission.
- The second mission remains unchanged.

### Test 6 — Kill attribution across stacks

Given two issuers with active missions against the same target.

Expected:

- One bounty credits one mission in each stack.

### Test 7 — Automatic ready state

Given a mission with `contract_kills = 1` and no progress.

When one matching bounty is processed.

Expected:

- Progress becomes 1.
- State becomes `REDIRECTED`.
- The mission is removed from its stack.
- A subsequent bounty does not credit the same mission again.

### Test 8 — Redirect event

Given an active mission with partial progress and a `MissionRedirected` event.

Expected:

- Progress becomes the full contract requirement.
- State becomes `REDIRECTED`.
- Repeating the event is harmless.

### Test 9 — Completion and failure

Given active missions and `MissionCompleted`, `MissionAbandoned`, and `MissionFailed` events.

Expected:

- Completed missions use the completion event reward.
- Abandoned and failed missions have reward zero.
- All three leave the active stack and appear in the Completed view.

### Test 10 — Reconciliation

Given tracked missions A and B, and a `Missions` event listing only A in Active/Complete/Failed.

Expected:

- A is unchanged.
- B becomes `COMPLETED`.
- No new missions are created.

### Test 11 — Different commanders

Given two concurrent files with different FIDs.

Expected:

- Missions, stacks, bounty feeds, and stations remain isolated.
- The UI can switch between commanders.

### Test 12 — Same commander, multiple files

Given two concurrent files with the same FID.

Expected:

- Both feed the same mission tracker.
- Station context remains per file.
- A mission accepted in file A uses file A's station.

### Test 13 — New file after startup

Given a new `Journal.*.log` file created after startup.

Expected:

- It is discovered automatically.
- It is read from offset zero.
- Its events are applied.

### Test 14 — Append to an old file

Given an existing file that receives new lines after startup.

Expected:

- Only the appended lines are processed.
- Previously processed events are not duplicated.

### Test 15 — Partial line

Given a JSON line split across two polls.

Expected:

- No parse error.
- The event is processed once after the newline arrives.

### Test 16 — Truncation

Given a file that shrinks below its stored offset.

Expected:

- The file is reprocessed from offset zero.
- The app does not crash.

### Test 17 — Bounty filters

Given bounties with:

- `faction_none` in victim faction.
- `faction_Pirate` in victim faction.
- `suit` in target.
- Zero or negative total reward.

Expected:

- None of them affect mission progress.

### Test 18 — Stateless restart

Given the same 8-day journals and a running app.

When the app exits and restarts.

Expected:

- The same active, redirected, and recently completed missions are reconstructed.
- No state file was created.
- Clipboard contents are gone.

### Test 19 — Summary calculations

Given a target faction with two issuing factions:

```text
Issuer A: missions requiring 30 and 20 kills, progress 10 and 5
Issuer B: mission requiring 40 kills, progress 40, redirected
```

Expected:

- Largest stack contract kills = 50.
- Remaining required = 35 (A: 35, B: 0; max is 35).
- Total contract kills = 90.
- Overlap ratio = 90 / 50 = 1.8.
- Total reward is the sum of included mission rewards.
- Turn-in reward includes only the redirected mission.
- Kills-to-next uses the oldest active mission per issuer.

### Test 20 — No writes

Run the app, process events, exit.

Expected:

- No mission, bounty, session, or clipboard files are created.

---

## 17. Out of scope

- Legacy/Horizons 3.x sessions.
- Persisting mission state.
- Importing or exporting existing tracker data.
- Mission-board scraping.
- Trade, exploration, engineering, or fleet-carrier data.
- Network calls.
- Multi-user synchronisation.
- Exact visual parity with any existing application.
- Reproducing historical missions older than the 8-day window.

---

## 18. Deliverables

- A Python package with an executable entry point.
- CLI options for journal directory, day window, poll interval, commander filter, and colour.
- In-memory domain model and journal ingestion.
- Multi-file, multi-session tailing.
- Textual (or equivalent) terminal UI.
- Automated tests using synthetic journal fixtures.
- A README covering setup, usage, configuration, and limitations.

**Clean-room constraint:** implement only from this specification and public Elite Dangerous journal documentation. Do not inspect or copy any existing massacre-mission tracker implementation.
