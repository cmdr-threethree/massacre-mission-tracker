from __future__ import annotations

from massacre_tracker.domain.models import Mission, MissionState
from massacre_tracker.ui.clipboard import ClipboardStore
from tests.builders import BASE_TIME


def mission() -> Mission:
    return Mission(
        mission_id=1,
        source_system="Sol",
        system_address=1,
        source_station="Galileo",
        issuing_faction="Issuer",
        destination_system="Achenar",
        target_faction="Pirates",
        localised_name="Kill pirates",
        reward=1000,
        contract_kills=10,
        progress_kills=0,
        wing=False,
        accepted_at=BASE_TIME,
        expires_at=None,
        state=MissionState.ACTIVE,
    )


def test_clipboard_adds_deduplicated_source_triples():
    store = ClipboardStore()
    assert store.add_source(mission())
    assert not store.add_source(mission())
    assert len(store.entries) == 1
    entry = store.entries[0]
    assert entry.destination_system == "Achenar"
    assert entry.source_system == "Sol"
    assert entry.source_station == "Galileo"


def test_clipboard_is_in_memory_and_clears():
    store = ClipboardStore()
    store.add_source(mission())
    store.copy("Sol")
    assert store.last_copied == "Sol"
    assert "Sol" in store.render()
    store.clear()
    assert store.entries == []
    assert store.last_copied is None
