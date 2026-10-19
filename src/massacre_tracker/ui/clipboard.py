"""In-memory clipboard for mission sources."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import Mission


@dataclass(frozen=True, slots=True)
class SourceTriple:
    destination_system: str
    source_system: str | None
    source_station: str | None

    @classmethod
    def from_mission(cls, mission: Mission) -> SourceTriple:
        return cls(
            destination_system=mission.destination_system or "",
            source_system=mission.source_system,
            source_station=mission.source_station,
        )

    def render(self) -> str:
        return (
            f"Destination: {self.destination_system or '—'} | "
            f"Source system: {self.source_system or '—'} | "
            f"Source station: {self.source_station or '—'}"
        )


@dataclass(slots=True)
class ClipboardStore:
    entries: list[SourceTriple] = field(default_factory=list)
    last_copied: str | None = None

    def add_source(self, mission: Mission) -> bool:
        triple = SourceTriple.from_mission(mission)
        if triple in self.entries:
            return False
        self.entries.append(triple)
        return True

    def copy(self, value: str) -> None:
        self.last_copied = value

    def clear(self) -> None:
        self.entries.clear()
        self.last_copied = None

    def render(self) -> str:
        lines = ["In-memory clipboard (discarded on exit)", ""]
        if self.last_copied:
            lines.extend([f"Last copied: {self.last_copied}", ""])
        if not self.entries:
            lines.append("No mission sources added yet. Select a mission and press y.")
        else:
            for index, entry in enumerate(self.entries, start=1):
                lines.append(f"{index}. {entry.render()}")
        return "\n".join(lines)
