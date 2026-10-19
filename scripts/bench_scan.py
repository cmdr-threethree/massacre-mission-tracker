#!/usr/bin/env python3
"""Synthetic journal scan benchmark.

Usage:
    python scripts/bench_scan.py --lines 500000 --files 8
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from massacre_tracker.config import Config
from massacre_tracker.engine import TrackerEngine
from massacre_tracker.journal.scanner import JournalScanner


def journal_line(name: str, timestamp: datetime, **fields) -> str:
    payload = {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "event": name,
    }
    payload.update(fields)
    return json.dumps(payload, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lines", type=int, default=500_000)
    parser.add_argument("--files", type=int, default=8)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        for file_index in range(args.files):
            stamp = (now - timedelta(hours=file_index)).strftime("%Y-%m-%dT%H%M%S")
            path = directory / f"Journal.{stamp}.{file_index + 1:02d}.log"
            lines = [
                journal_line(
                    "Fileheader",
                    now - timedelta(days=7),
                    gameversion="4.0.0.100",
                ),
                journal_line(
                    "Commander",
                    now - timedelta(days=7),
                    Name=f"Bench{file_index}",
                    FID=f"F{file_index}",
                ),
                journal_line(
                    "Docked",
                    now - timedelta(days=7),
                    StarSystem="Sol",
                    SystemAddress=1,
                    StationName="Galileo",
                ),
            ]
            per_file = max(1, args.lines // args.files)
            for index in range(per_file):
                timestamp = now - timedelta(minutes=index % 10000)
                if index % 1000 == 0:
                    lines.append(
                        journal_line(
                            "MissionAccepted",
                            timestamp,
                            MissionID=file_index * 1_000_000 + index,
                            Faction=f"Issuer {index % 5}",
                            DestinationSystem="Sol",
                            TargetFaction=f"Target {index % 7}",
                            LocalisedName="Massacre",
                            Reward=10_000_000,
                            KillCount=30,
                            Wing=False,
                            TargetType="MissionUtil_FactionTag_Pirate",
                        )
                    )
                elif index % 100 == 1:
                    lines.append(
                        journal_line(
                            "Bounty",
                            timestamp,
                            Target="sidewinder",
                            VictimFaction=f"Target {index % 7}",
                            TotalReward=100_000,
                        )
                    )
                else:
                    lines.append(
                        journal_line(
                            "FSDJump",
                            timestamp,
                            StarSystem=f"System {index % 1000}",
                            JumpDist=12.3,
                            FuelUsed=1.2,
                            FuelLevel=30.0,
                        )
                    )
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        engine = TrackerEngine()
        config = Config(journal_dir=directory, days=8)
        started = time.perf_counter()
        report = JournalScanner(engine, config, clock=lambda: now).scan()
        elapsed = time.perf_counter() - started

        print(f"files:            {report.files_total}")
        print(f"lines:            {report.lines_read:,}")
        print(f"relevant applied: {report.events_applied:,}")
        print(f"scan seconds:     {elapsed:.3f}")
        print(f"missions:         {sum(len(v.missions) for v in engine.commanders.values()):,}")


if __name__ == "__main__":
    main()
