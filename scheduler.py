from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from datetime import datetime
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


def generate_tee_times(start_time_str: str, group_count: int, interval: int = 8) -> List[str]:
    """
    Generates tee times based on number of groups.

    Example:
    start_time = 4:28
    group_count = 6

    → 4:28
    → 4:36
    → 4:44
    → 4:52
    → 5:00
    → 5:08
    """

    start = datetime.strptime(start_time_str, "%H:%M")

    times = []

    for i in range(group_count):
        t = start + timedelta(minutes=i * interval)
        times.append(t.strftime("%-I:%M"))

    return times


def generate_play_dates(start_date: date, end_date: date, weekdays: List[int]) -> List[date]:
    """
    Generate play dates between start and end date for selected weekdays.

    Weekday values:
    Monday=0 ... Sunday=6
    """

    dates = []
    current = start_date

    while current <= end_date:
        if current.weekday() in weekdays:
            dates.append(current)

        current += timedelta(days=1)

    return dates


def normalize_pair(a: Player, b: Player) -> Pair:
    return (a, b) if a <= b else (b, a)


def group_pairs(group: Sequence[Player]) -> List[Pair]:
    return [normalize_pair(a, b) for a, b in combinations(group, 2)]


def group_signature(group: Sequence[Player]) -> Tuple[Player, ...]:
    return tuple(sorted(group))


def all_expected_pairs(players: Sequence[Player]) -> List[Pair]:
    return [normalize_pair(a, b) for a, b in combinations(players, 2)]


def group_sizes_for_count(count: int) -> List[int]:
    """
    Build a division using groups of 4 where possible, and 3-somes when needed.

    Rules:
    - Prefer 4-somes
    - If remainder is 2, use two 3-somes instead of a 4-some + 2-some
    - If remainder is 3, use one 3-some
    - If remainder is 1, use three 3-somes (e.g. 13 = 4+3+3+3)

    Impossible counts with only 3s and 4s:
    - 1, 2, 5
    """
    if count == 0:
        return []

    if count in {1, 2, 5}:
        raise ValueError(
            f"{count} players in one division cannot be arranged using only 3-somes and 4-somes."
        )

    remainder = count % 4

    if remainder == 0:
        return [4] * (count // 4)

    if remainder == 2:
        # e.g. 6 => 3+3, 10 => 4+3+3, 14 => 4+4+3+3
        return [4] * ((count - 6) // 4) + [3, 3]

    if remainder == 3:
        # e.g. 7 => 4+3, 11 => 4+4+3
        return [4] * ((count - 3) // 4) + [3]

    # remainder == 1
    # e.g. 9 => 3+3+3, 13 => 4+3+3+3
    if count < 9:
        raise ValueError(
            f"{count} players in one division cannot be arranged using only 3-somes and 4-somes."
        )
    return [4] * ((count - 9) // 4) + [3, 3, 3]


def score_group(
    group: Sequence[Player],
    pair_counts: Dict[Pair, int],
    group_counts: Dict[Tuple[Player, ...], int],
    expected_pairs_set: set[Pair],
) -> int:
    """
    Lower score is better.
    Rewards unseen pairs and penalizes repeated pairs / repeated exact groups.
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

    score += repeated_pair_penalty
    score += exact_group_repeat_penalty
    score -= unseen_pairs * 200

    return score


def score_teetime_assignment(
    time_str: str,
    group: Sequence[Player],
    tee_time_counts: Dict[Player, Dict[str, int]],
) -> int:
    score = 0
    for player in group:
        score += tee_time_counts[player].get(time_str, 0) * 20
    return score


def generate_candidate_partition(
    players: Sequence[Player],
    group_sizes: Sequence[int],
) -> List[Group]:
    shuffled = list(players)
    random.shuffle(shuffled)

    groups: List[Group] = []
    idx = 0
    for size in group_sizes:
        groups.append(shuffled[idx : idx + size])
        idx += size
    return groups


def optimize_groups(
    players: Sequence[Player],
    group_sizes: Sequence[int],
    pair_counts: Dict[Pair, int],
    group_counts: Dict[Tuple[Player, ...], int],
    expected_pairs_set: set[Pair],
    tries: int,
) -> List[Group]:
    if not players:
        return []

    best_groups: List[Group] | None = None
    best_score: int | None = None

    for _ in range(tries):
        groups = generate_candidate_partition(players, group_sizes)
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


def interleave_groups(
    men_groups: List[Group],
    women_groups: List[Group],
) -> List[Tuple[str, Group]]:
    """
    Alternate groups as much as possible, starting with whichever division
    currently has more groups. If tied, start with men.
    """
    men_queue = list(men_groups)
    women_queue = list(women_groups)

    if len(men_queue) >= len(women_queue):
        turn = "men"
    else:
        turn = "women"

    ordered: List[Tuple[str, Group]] = []

    while men_queue or women_queue:
        if turn == "men":
            if men_queue:
                ordered.append(("men", men_queue.pop(0)))
            turn = "women"
            if not women_queue and men_queue:
                turn = "men"
        else:
            if women_queue:
                ordered.append(("women", women_queue.pop(0)))
            turn = "men"
            if not men_queue and women_queue:
                turn = "women"

    return ordered


def order_groups_by_mode(
    men_groups: List[Group],
    women_groups: List[Group],
    placement_mode: PlacementMode,
    week_num: int,
) -> List[Tuple[str, Group]]:
    """
    Generic placement for any number of men/women groups.
    """
    if placement_mode == "women_first":
        return [("women", g) for g in women_groups] + [("men", g) for g in men_groups]

    if placement_mode == "women_last":
        return [("men", g) for g in men_groups] + [("women", g) for g in women_groups]

    # Treat all mixed styles as alternating for flexible group counts.
    ordered = interleave_groups(men_groups, women_groups)

    # Rotate weekly so women are not stuck in the same slots.
    if ordered:
        shift = (week_num - 1) % len(ordered)
        ordered = ordered[shift:] + ordered[:shift]

    return ordered


def optimize_slot_assignment(
    ordered_groups: List[Tuple[str, Group]],
    tee_times: Sequence[str],
    tee_time_counts: Dict[Player, Dict[str, int]],
) -> List[Tuple[str, str, Group]]:
    if len(ordered_groups) > len(tee_times):
        raise ValueError(
            f"Not enough tee times. Need {len(ordered_groups)}, got {len(tee_times)}."
        )

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


def score_season(season, men_players, women_players):

    pair_counts = {}
    group_counts = {}
    tee_counts = {}

    players = list(men_players) + list(women_players)

    for p in players:
        tee_counts[p] = {}

    for week in season:
        for slot in week.slots:

            group = slot["group"]
            time = slot["time"]

            #
            # track tee times
            #

            for p in group:
                tee_counts[p][time] = tee_counts[p].get(time,0) + 1

            #
            # track groups
            #

            sig = tuple(sorted(group))
            group_counts[sig] = group_counts.get(sig,0) + 1

            #
            # track pairs
            #

            for a,b in combinations(group,2):
                pair = tuple(sorted((a,b)))
                pair_counts[pair] = pair_counts.get(pair,0) + 1

    score = 0

    #
    # penalize repeated pairs
    #

    for count in pair_counts.values():
        if count > 1:
            score += (count-1) * 40

    #
    # penalize repeated foursomes
    #

    for count in group_counts.values():
        if count > 1:
            score += (count-1) * 200

    #
    # penalize tee time imbalance
    #

    for player,times in tee_counts.items():

        if not times:
            continue

        counts = list(times.values())

        max_time = max(counts)
        min_time = min(counts)

        imbalance = max_time - min_time

        score += imbalance * 25

    return score

def _generate_schedule_once(
    men_players: Sequence[Player],
    women_players: Sequence[Player],
    start_date: date,
    end_date: date,
    first_tee_time: str,
    weekdays: List[int],
    placement_mode: PlacementMode = "mixed_alternating",
    men_group_tries: int = 4000,
    women_group_tries: int = 1500,
    seed: int = 42,
) -> List[WeekSchedule]:
    random.seed(seed)

    dates = generate_play_dates(start_date, end_date, weekdays)

    men_group_sizes = group_sizes_for_count(len(men_players))
    women_group_sizes = group_sizes_for_count(len(women_players))
    total_groups = len(men_group_sizes) + len(women_group_sizes)

    tee_times = generate_tee_times(first_tee_time, total_groups)

    pair_counts: Dict[Pair, int] = {}
    group_counts: Dict[Tuple[Player, ...], int] = {}
    tee_time_counts: Dict[Player, Dict[str, int]] = defaultdict(dict)

    expected_pairs_men = set(all_expected_pairs(men_players))
    expected_pairs_women = set(all_expected_pairs(women_players))

    season: List[WeekSchedule] = []

    for week_num, play_date in enumerate(dates, start=1):
        men_groups = optimize_groups(
            players=men_players,
            group_sizes=men_group_sizes,
            pair_counts=pair_counts,
            group_counts=group_counts,
            expected_pairs_set=expected_pairs_men,
            tries=men_group_tries,
        )

        women_groups = optimize_groups(
            players=women_players,
            group_sizes=women_group_sizes,
            pair_counts=pair_counts,
            group_counts=group_counts,
            expected_pairs_set=expected_pairs_women,
            tries=women_group_tries,
        )

        ordered_groups = order_groups_by_mode(
            men_groups=men_groups,
            women_groups=women_groups,
            placement_mode=placement_mode,
            week_num=week_num,
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

def generate_schedule(
    men_players,
    women_players,
    start_date,
    end_date,
    first_tee_time,
    weekdays,
    placement_mode="mixed_alternating",
    tries=5
):

    best_season = None
    best_score = None

    for i in range(tries):

        season = _generate_schedule_once(
            men_players=men_players,
            women_players=women_players,
            start_date=start_date,
            end_date=end_date,
            first_tee_time=first_tee_time,
            weekdays=weekdays,
            placement_mode=placement_mode,
            seed=random.randint(1,1000000)
        )

        score = score_season(season, men_players, women_players)

        if best_score is None or score < best_score:
            best_score = score
            best_season = season

    return best_season

def summarize_schedule(
    season: Sequence[WeekSchedule],
    men_players: Sequence[Player],
    women_players: Sequence[Player],
) -> Dict[str, object]:
    pair_counts: Dict[Pair, int] = {}
    tee_time_counts: DefaultDict[Player, Dict[str, int]] = defaultdict(dict)
    group_counts: Dict[Tuple[Player, ...], int] = {}

    tee_times = [str(slot["time"]) for slot in season[0].slots] if season else []

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
        if not expected:
            return {
                "unique_pairs_total": 0,
                "unique_pairs_seen": 0,
                "coverage_pct": 100.0,
                "unseen_pairs": [],
                "max_pair_repeat": 0,
            }

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
            player: {time: tee_time_counts[player].get(time, 0) for time in tee_times}
            for player in players
        }

    repeat_groups = {
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
        "repeat_groups": repeat_groups,
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
        first_tee_time="16:28",
        placement_mode="mixed_alternating",
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