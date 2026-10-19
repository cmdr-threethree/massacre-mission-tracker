"""Display formatting helpers for the terminal UI."""

from __future__ import annotations

from datetime import datetime, timezone

from ..domain.models import Mission, MissionState

_SHIP_NAMES = {
    "adder": "Adder",
    "anaconda": "Anaconda",
    "asp": "Asp Explorer",
    "asp_scout": "Asp Scout",
    "beluga": "Beluga Liner",
    "cobramkiii": "Cobra Mk III",
    "cobramkiv": "Cobra Mk IV",
    "corsair": "Corsair",
    "diamondback": "Diamondback Scout",
    "diamondbackxl": "Diamondback Explorer",
    "dolphin": "Dolphin",
    "eagle": "Eagle",
    "empire_courier": "Imperial Courier",
    "empire_eagle": "Imperial Eagle",
    "empire_trader": "Imperial Clipper",
    "federation_assault_ship": "Federal Assault Ship",
    "federation_corvette": "Federal Corvette",
    "federation_dropship": "Federal Dropship",
    "federation_gunship": "Federal Gunship",
    "ferdelance": "Fer-de-Lance",
    "hauler": "Hauler",
    "krait_light": "Krait Phantom",
    "krait_mkii": "Krait Mk II",
    "mamba": "Mamba",
    "mandalay": "Mandalay",
    "orca": "Orca",
    "python": "Python",
    "python_nx": "Python Mk II",
    "sidewinder": "Sidewinder",
    "type6": "Type-6 Transporter",
    "type7": "Type-7 Transporter",
    "type8": "Type-8 Transporter",
    "type9": "Type-9 Heavy",
    "type9_military": "Type-10 Defender",
    "type10": "Type-10 Defender",
    "viper": "Viper Mk III",
    "viper_mkiv": "Viper Mk IV",
    "vulture": "Vulture",
}


def format_credits(value: int) -> str:
    return f"{value:,} cr"


def format_expiry(value: datetime | None) -> str:
    if value is None:
        return "—"
    local = value.astimezone()
    return local.strftime("%Y-%m-%d %H:%M")


def format_local_time(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone().strftime("%H:%M:%S")


def format_age(value: datetime | None, now: datetime | None = None) -> str:
    if value is None:
        return "—"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = max(0.0, (current - value).total_seconds())
    return format_seconds(seconds)


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    value = max(0.0, seconds)
    if value < 60:
        return f"{value:.0f}s"
    if value < 3600:
        return f"{value / 60:.0f}m"
    if value < 86400:
        return f"{value / 3600:.1f}h"
    return f"{value / 86400:.1f}d"


def format_wait(delta: float | None) -> str:
    return format_seconds(delta)


def format_ship(identifier: str) -> str:
    if not identifier:
        return "—"
    key = identifier.casefold()
    if key in _SHIP_NAMES:
        return _SHIP_NAMES[key]
    return identifier.replace("_", " ").title()


def mission_kills(mission: Mission) -> str:
    return f"{mission.progress_kills} / {mission.contract_kills}"


def state_color(state: MissionState) -> str:
    if state == MissionState.ACTIVE:
        return "green"
    if state == MissionState.REDIRECTED:
        return "bold bright_green"
    if state == MissionState.COMPLETED:
        return "cyan"
    if state == MissionState.ABANDONED:
        return "yellow"
    return "red"


def is_ready(mission: Mission, station_names: frozenset[str]) -> bool:
    return mission.state == MissionState.REDIRECTED and mission.is_ready_to_turn_in(
        set(station_names)
    )
