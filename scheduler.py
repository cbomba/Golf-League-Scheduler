from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Literal, Sequence, Tuple

Player = str
Pair = Tuple[Player, Player]
Group = List[Player]
PlacementMode = Literal[
    "women_first",
    "women_last",
    "mixed_alternating",
    "mixed_front_back",
    "split_middle",
]


@dataclass
class WeekSchedule:
    week_num: int
    play_date: str
    slots: List[Dict[str, object]]


TEE_TIMES = ["4:28", "4:36", "4:44", "4:52", "5:00", "5:08"]


def daterange_wednesdays(start_date: date, end_date: date) -> List[date]:
    dates: List[date] = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=7)
    return dates


def normalize_pair(a: Player, b: Player) -> Pair:
    return (a, b) if a <= b else (b, a)


def group_pairs(group: Sequence[Player]) -> List[Pair]:
    return [normalize_pair(a, b) for a, b in combinations(group, 2)]


def group_signature(group: Sequence[Player]) -> Tuple[Player, ...]:
    return tuple(sorted(group))


def all_expected_pairs(players: Sequence[Player]) -> List[Pair]:
    return [normalize_pair(a, b) for a, b in combinations(players, 2)]


def score_group(
    group: Sequence[Player],
    pair_counts: Dict[Pair, int],
    group_counts: Dict[Tuple[Player, ...], int],
    expected_pairs_set: set[Pair],
) -> int:
    """
    Lower score is better.
    Strongly rewards unseen pairs.
    Penalizes repeated pairs and repeated exact foursomes.
    """
    score = 0

    unseen_pairs = 0
    repeated_pair_penalty = 0

    for pair in group_pairs(group):
        count = pair_counts.get(pair, 0)
        if pair in expected_pairs_set and count == 0:
            unseen_pairs += 1
        repeated_pair_penalty += count * 25

    sig = group_signature(group)
    exact_group_repeat_penalty = group_counts.get(sig, 0) * 150

    # Reward unseen pairs heavily by subtracting points
    score += repeated_pair_penalty
    score += exact_group_repeat_penalty
    score -= unseen_pairs * 200

    return score


def score_teetime_assignment(
    time_str: str,
    group: Sequence[Player],
    tee_time_counts: Dict[Player, Dict[str, int]],
) -> int:
    """
    Lower score is better.
    Penalizes putting people in tee times they have already used a lot.
    """
    score = 0
    for player in group:
        score += tee_time_counts[player].get(time_str, 0) * 20

    # Slight bias for overall spread between earliest/latest over time
    return score


def generate_candidate_partition(
    players: Sequence[Player],
    group_count: int,
    group_size: int,
) -> List[Group]:
    shuffled = list(players)
    random.shuffle(shuffled)
    return [
        shuffled[i * group_size : (i + 1) * group_size]
        for i in range(group_count)
    ]


def optimize_groups(
    players: Sequence[Player],
    group_count: int,
    group_size: int,
    pair_counts: Dict[Pair, int],
    group_counts: Dict[Tuple[Player, ...], int],
    expected_pairs_set: set[Pair],
    tries: int,
) -> List[Group]:
    best_groups: List[Group] | None = None
    best_score: int | None = None

    for _ in range(tries):
        groups = generate_candidate_partition(players, group_count, group_size)
        total_score = sum(
            score_group(g, pair_counts, group_counts, expected_pairs_set)
            for g in groups
        )

        if best_score is None or total_score < best_score:
            best_score = total_score
            best_groups = groups

    if best_groups is None:
        raise RuntimeError("Failed to generate optimized groups.")

    return best_groups


def order_groups_by_mode(
    men_groups: List[Group],
    women_groups: List[Group],
    placement_mode: PlacementMode,
) -> List[Tuple[str, Group]]:
    """
    Returns list of (division, group) in slot order before tee-time fairness assignment.
    division = 'men' or 'women'
    """
    if placement_mode == "women_first":
        return [("women", g) for g in women_groups] + [("men", g) for g in men_groups]

    if placement_mode == "women_last":
        return [("men", g) for g in men_groups] + [("women", g) for g in women_groups]

    if placement_mode == "mixed_alternating":
        # women in positions 1 and 3
        ordered: List[Tuple[str, Group]] = []
        ordered.append(("men", men_groups[0]))
        ordered.append(("women", women_groups[0]))
        ordered.append(("men", men_groups[1]))
        ordered.append(("women", women_groups[1]))
        ordered.append(("men", men_groups[2]))
        ordered.append(("men", men_groups[3]))
        return ordered

    if placement_mode == "mixed_front_back":
        # women in 2 and 5
        return [
            ("men", men_groups[0]),
            ("women", women_groups[0]),
            ("men", men_groups[1]),
            ("men", men_groups[2]),
            ("women", women_groups[1]),
            ("men", men_groups[3]),
        ]

    if placement_mode == "split_middle":
        # women in the middle
        return [
            ("men", men_groups[0]),
            ("men", men_groups[1]),
            ("women", women_groups[0]),
            ("women", women_groups[1]),
            ("men", men_groups[2]),
            ("men", men_groups[3]),
        ]

    raise ValueError(f"Unsupported placement mode: {placement_mode}")


def optimize_slot_assignment(
    ordered_groups: List[Tuple[str, Group]],
    tee_times: Sequence[str],
    tee_time_counts: Dict[Player, Dict[str, int]],
) -> List[Tuple[str, str, Group]]:
    """
    Assigns actual tee times to the pre-ordered groups while respecting the
    women's placement pattern. This means we optimize *within* the chosen
    time slots rather than ignoring the requested pattern.
    """
    if len(ordered_groups) != len(tee_times):
        raise ValueError("Number of groups must match number of tee times.")

    # Group positions are fixed by placement mode.
    # We only optimize mapping by evaluating each fixed slot.
    result: List[Tuple[str, str, Group]] = []
    for idx, (division, group) in enumerate(ordered_groups):
        time_str = tee_times[idx]
        result.append((time_str, division, group))

    return result


def weekly_local_improvement(
    assignments: List[Tuple[str, str, Group]],
    tee_time_counts: Dict[Player, Dict[str, int]],
    pair_counts: Dict[Pair, int],
    group_counts: Dict[Tuple[Player, ...], int],
    expected_pairs_set_men: set[Pair],
    expected_pairs_set_women: set[Pair],
    max_passes: int = 20,
) -> List[Tuple[str, str, Group]]:
    """
    Improve tee-time fairness by swapping same-division groups only,
    so women/men placement rules remain intact.
    """
    current = assignments[:]

    def total_cost(schedule: List[Tuple[str, str, Group]]) -> int:
        total = 0
        for time_str, division, group in schedule:
            total += score_teetime_assignment(time_str, group, tee_time_counts)
            expected = expected_pairs_set_men if division == "men" else expected_pairs_set_women
            total += score_group(group, pair_counts, group_counts, expected)
        return total

    improved = True
    passes = 0
    while improved and passes < max_passes:
        improved = False
        passes += 1
        base_cost = total_cost(current)

        for i in range(len(current)):
            for j in range(i + 1, len(current)):
                time_i, div_i, group_i = current[i]
                time_j, div_j, group_j = current[j]

                if div_i != div_j:
                    continue

                candidate = current[:]
                candidate[i] = (time_i, div_i, group_j)
                candidate[j] = (time_j, div_j, group_i)

                cand_cost = total_cost(candidate)
                if cand_cost < base_cost:
                    current = candidate
                    base_cost = cand_cost
                    improved = True

    return current


def update_tracking(
    assignments: Iterable[Tuple[str, str, Group]],
    pair_counts: Dict[Pair, int],
    group_counts: Dict[Tuple[Player, ...], int],
    tee_time_counts: Dict[Player, Dict[str, int]],
) -> None:
    for time_str, _division, group in assignments:
        sig = group_signature(group)
        group_counts[sig] = group_counts.get(sig, 0) + 1

        for pair in group_pairs(group):
            pair_counts[pair] = pair_counts.get(pair, 0) + 1

        for player in group:
            tee_time_counts[player][time_str] = tee_time_counts[player].get(time_str, 0) + 1


def generate_schedule(
    men_players: Sequence[Player],
    women_players: Sequence[Player],
    start_date: date,
    end_date: date,
    tee_times: Sequence[str],
    placement_mode: PlacementMode = "mixed_alternating",
    men_group_tries: int = 4000,
    women_group_tries: int = 1500,
    seed: int = 42,
) -> List[WeekSchedule]:
    random.seed(seed)

    dates = daterange_wednesdays(start_date, end_date)

    if len(men_players) != 16:
        raise ValueError("This version currently expects exactly 16 men.")
    if len(women_players) != 8:
        raise ValueError("This version currently expects exactly 8 women.")
    if len(tee_times) != 6:
        raise ValueError("This version currently expects exactly 6 tee times.")

    pair_counts: Dict[Pair, int] = {}
    group_counts: Dict[Tuple[Player, ...], int] = {}
    tee_time_counts: Dict[Player, Dict[str, int]] = defaultdict(dict)

    expected_pairs_men = set(all_expected_pairs(men_players))
    expected_pairs_women = set(all_expected_pairs(women_players))

    season: List[WeekSchedule] = []

    for week_num, play_date in enumerate(dates, start=1):
        men_groups = optimize_groups(
            players=men_players,
            group_count=4,
            group_size=4,
            pair_counts=pair_counts,
            group_counts=group_counts,
            expected_pairs_set=expected_pairs_men,
            tries=men_group_tries,
        )

        women_groups = optimize_groups(
            players=women_players,
            group_count=2,
            group_size=4,
            pair_counts=pair_counts,
            group_counts=group_counts,
            expected_pairs_set=expected_pairs_women,
            tries=women_group_tries,
        )

        ordered_groups = order_groups_by_mode(
            men_groups=men_groups,
            women_groups=women_groups,
            placement_mode=placement_mode,
        )

        assignments = optimize_slot_assignment(
            ordered_groups=ordered_groups,
            tee_times=tee_times,
            tee_time_counts=tee_time_counts,
        )

        assignments = weekly_local_improvement(
            assignments=assignments,
            tee_time_counts=tee_time_counts,
            pair_counts=pair_counts,
            group_counts=group_counts,
            expected_pairs_set_men=expected_pairs_men,
            expected_pairs_set_women=expected_pairs_women,
        )

        update_tracking(
            assignments=assignments,
            pair_counts=pair_counts,
            group_counts=group_counts,
            tee_time_counts=tee_time_counts,
        )

        slots: List[Dict[str, object]] = []
        for time_str, division, group in assignments:
            slots.append(
                {
                    "time": time_str,
                    "division": division,
                    "group": group,
                    "display": "-".join(group),
                }
            )

        season.append(
            WeekSchedule(
                week_num=week_num,
                play_date=play_date.strftime("%-m/%-d/%y"),
                slots=slots,
            )
        )

    return season


def summarize_schedule(
    season: Sequence[WeekSchedule],
    men_players: Sequence[Player],
    women_players: Sequence[Player],
) -> Dict[str, object]:
    pair_counts: Dict[Pair, int] = {}
    tee_time_counts: DefaultDict[Player, Dict[str, int]] = defaultdict(dict)
    group_counts: Dict[Tuple[Player, ...], int] = {}

    for week in season:
        for slot in week.slots:
            time_str = str(slot["time"])
            group = list(slot["group"])  # type: ignore

            sig = group_signature(group)
            group_counts[sig] = group_counts.get(sig, 0) + 1

            for pair in group_pairs(group):
                pair_counts[pair] = pair_counts.get(pair, 0) + 1

            for player in group:
                tee_time_counts[player][time_str] = tee_time_counts[player].get(time_str, 0) + 1

    def coverage(players: Sequence[Player]) -> Dict[str, object]:
        expected: List[Pair] = all_expected_pairs(players)
        unseen = [p for p in expected if pair_counts.get(p, 0) == 0]
        return {
            "unique_pairs_total": len(expected),
            "unique_pairs_seen": len(expected) - len(unseen),
            "coverage_pct": round(((len(expected) - len(unseen)) / len(expected)) * 100, 2),
            "unseen_pairs": unseen,
            "max_pair_repeat": max((pair_counts.get(p, 0) for p in expected), default=0),
        }

    def tee_summary(players: Sequence[Player]) -> Dict[str, Dict[str, int]]:
        return {
            player: {time: tee_time_counts[player].get(time, 0) for time in TEE_TIMES}
            for player in players
        }

    repeat_foursomes = {
        "-".join(sig): count for sig, count in group_counts.items() if count > 1
    }

    return {
        "weeks": len(season),
        "men_coverage": coverage(men_players),
        "women_coverage": coverage(women_players),
        "tee_time_distribution": {
            "men": tee_summary(men_players),
            "women": tee_summary(women_players),
        },
        "repeat_foursomes": repeat_foursomes,
    }


def export_csv(season: Sequence[WeekSchedule], filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with filepath.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Week", "Date", "Time", "Division", "Group"])

        for week in season:
            for slot in week.slots:
                writer.writerow(
                    [
                        week.week_num,
                        week.play_date,
                        slot["time"],
                        slot["division"],
                        slot["display"],
                    ]
                )


def export_json(
    season: Sequence[WeekSchedule],
    summary: Dict[str, object],
    filepath: Path,
) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "season": [
            {
                "week_num": week.week_num,
                "play_date": week.play_date,
                "slots": week.slots,
            }
            for week in season
        ],
        "summary": summary,
    }

    with filepath.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def print_console_view(season: Sequence[WeekSchedule]) -> None:
    for week in season:
        print(f"\nWeek {week.week_num} - {week.play_date}")
        for slot in week.slots:
            print(
                f"  {slot['time']:>5}  "
                f"{str(slot['division']).upper():<5}  "
                f"{slot['display']}"
            )


if __name__ == "__main__":
    men = [str(i) for i in range(1, 17)]
    women = list("ABCDEFGH")

    season = generate_schedule(
        men_players=men,
        women_players=women,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 9, 16),
        tee_times=TEE_TIMES,
        placement_mode="mixed_alternating",  # change this here
        men_group_tries=4000,
        women_group_tries=1500,
        seed=42,
    )

    summary = summarize_schedule(season, men, women)

    output_dir = Path("./outputs")
    export_csv(season, output_dir / "gold_league_schedule.csv")
    export_json(season, summary, output_dir / "gold_league_schedule.json")

    print_console_view(season)

    print("\nSummary")
    print(json.dumps(summary, indent=2))
    print(f"\nCSV saved to: {output_dir / 'gold_league_schedule.csv'}")
    print(f"JSON saved to: {output_dir / 'gold_league_schedule.json'}")