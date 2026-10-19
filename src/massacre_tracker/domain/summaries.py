"""Derived stack and target-faction summaries."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .models import CommanderTracker, Mission, MissionState


@dataclass(frozen=True, slots=True)
class StackSummary:
    issuing_faction: str
    target_faction: str
    mission_count: int
    contract_kills: int
    deficit: int
    reward: int
    oldest_first: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TargetSummary:
    target_faction: str
    mission_count: int
    active_mission_count: int
    max_stack_contract: int
    total_contract: int
    overlap_ratio: float
    remaining_required: int
    kills_to_next_completion: int
    total_reward: int
    shareable_reward: int
    turn_in_reward: int
    shareable_turn_in_reward: int
    average_per_unique_kill: int
    average_per_mission: int


def build_stack_summaries(tracker: CommanderTracker) -> list[StackSummary]:
    groups: dict[tuple[str, str], list[Mission]] = defaultdict(list)
    for mission in tracker.missions.values():
        if mission.state != MissionState.ACTIVE:
            continue
        key = (
            mission.issuing_faction.casefold(),
            mission.target_faction.casefold(),
        )
        groups[key].append(mission)

    provisional: list[tuple[str, str, list[Mission]]] = []
    for key in sorted(groups):
        missions = groups[key]
        oldest = min(missions, key=lambda m: m.sort_key)
        provisional.append(
            (oldest.issuing_faction, oldest.target_faction, missions)
        )

    largest_by_target: dict[str, int] = {}
    for _, target, missions in provisional:
        contract = sum(m.contract_kills for m in missions)
        target_key = target.casefold()
        largest_by_target[target_key] = max(
            largest_by_target.get(target_key, 0), contract
        )

    summaries: list[StackSummary] = []
    for issuer, target, missions in provisional:
        contract = sum(m.contract_kills for m in missions)
        ordered = sorted(missions, key=lambda m: m.sort_key)
        summaries.append(
            StackSummary(
                issuing_faction=issuer,
                target_faction=target,
                mission_count=len(missions),
                contract_kills=contract,
                deficit=largest_by_target.get(target.casefold(), contract) - contract,
                reward=sum(m.reward for m in missions),
                oldest_first=tuple(m.mission_id for m in ordered),
            )
        )
    return summaries


def build_target_summaries(tracker: CommanderTracker) -> list[TargetSummary]:
    included = [
        mission
        for mission in tracker.missions.values()
        if mission.is_summary_member
    ]
    by_target: dict[str, list[Mission]] = defaultdict(list)
    for mission in included:
        by_target[mission.target_faction.casefold()].append(mission)

    summaries: list[TargetSummary] = []
    for target_key in sorted(by_target):
        missions = by_target[target_key]
        oldest = min(missions, key=lambda m: m.sort_key)

        by_issuer: dict[str, list[Mission]] = defaultdict(list)
        for mission in missions:
            by_issuer[mission.issuing_faction.casefold()].append(mission)

        per_issuer_contract: dict[str, int] = {}
        per_issuer_remaining: dict[str, int] = {}
        kills_to_next_candidates: list[int] = []

        for issuer_key, issuer_missions in by_issuer.items():
            per_issuer_contract[issuer_key] = sum(
                m.contract_kills for m in issuer_missions
            )
            per_issuer_remaining[issuer_key] = sum(
                m.remaining_kills for m in issuer_missions
            )
            active = [
                m for m in issuer_missions if m.state == MissionState.ACTIVE
            ]
            if active:
                next_mission = min(active, key=lambda m: m.sort_key)
                kills_to_next_candidates.append(next_mission.remaining_kills)

        max_stack_contract = max(per_issuer_contract.values(), default=0)
        remaining_required = max(per_issuer_remaining.values(), default=0)
        total_contract = sum(m.contract_kills for m in missions)
        total_reward = sum(m.reward for m in missions)
        mission_count = len(missions)

        summaries.append(
            TargetSummary(
                target_faction=oldest.target_faction,
                mission_count=mission_count,
                active_mission_count=sum(
                    1 for m in missions if m.state == MissionState.ACTIVE
                ),
                max_stack_contract=max_stack_contract,
                total_contract=total_contract,
                overlap_ratio=(
                    total_contract / max_stack_contract
                    if max_stack_contract
                    else 0.0
                ),
                remaining_required=remaining_required,
                kills_to_next_completion=(
                    min(kills_to_next_candidates)
                    if kills_to_next_candidates
                    else 0
                ),
                total_reward=total_reward,
                shareable_reward=sum(m.reward for m in missions if m.wing),
                turn_in_reward=sum(
                    m.reward
                    for m in missions
                    if m.state == MissionState.REDIRECTED
                ),
                shareable_turn_in_reward=sum(
                    m.reward
                    for m in missions
                    if m.state == MissionState.REDIRECTED and m.wing
                ),
                average_per_unique_kill=round_ratio(
                    total_reward, max_stack_contract
                ),
                average_per_mission=round_ratio(total_reward, mission_count),
            )
        )
    return summaries


def round_ratio(numerator: int, denominator: int) -> int:
    if denominator == 0:
        return 0
    value = Decimal(numerator) / Decimal(denominator)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
