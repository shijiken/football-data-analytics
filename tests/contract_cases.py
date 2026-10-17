"""Shared contract test cases: one source row per scenario, with the rules it violates.

Pure test data used by both the pipeline unit tests and the database parity tests, so a rule
cannot be tested on one side only (plan.md "tests/contract_cases.py").
"""

import dataclasses
import datetime as dt

#: A complete, valid row (data row 1 of ``matches_valid.csv``) as the 48 source strings.
FULL_ROW: dict[str, str] = {
    "Division": "E0",
    "MatchDate": "2020-09-12",
    "MatchTime": "15:00:00",
    "HomeTeam": "Arsenal",
    "AwayTeam": "Chelsea",
    "HomeElo": "1850.5",
    "AwayElo": "1820.25",
    "Form3Home": "4.0",
    "Form5Home": "7.0",
    "Form3Away": "3.0",
    "Form5Away": "6.0",
    "FTHome": "2.0",
    "FTAway": "1.0",
    "FTResult": "H",
    "HTHome": "1.0",
    "HTAway": "0.0",
    "HTResult": "H",
    "HomeShots": "14.0",
    "AwayShots": "9.0",
    "HomeTarget": "6.0",
    "AwayTarget": "3.0",
    "HomeFouls": "11.0",
    "AwayFouls": "12.0",
    "HomeCorners": "7.0",
    "AwayCorners": "4.0",
    "HomeYellow": "1.0",
    "AwayYellow": "2.0",
    "HomeRed": "0.0",
    "AwayRed": "0.0",
    "OddHome": "1.65",
    "OddDraw": "3.9",
    "OddAway": "5.25",
    "MaxHome": "1.7",
    "MaxDraw": "4.1",
    "MaxAway": "5.6",
    "Over25": "1.85",
    "Under25": "2.0",
    "MaxOver25": "1.95",
    "MaxUnder25": "2.1",
    "HandiSize": "-0.75",
    "HandiHome": "1.95",
    "HandiAway": "1.95",
    "C_LTH": "0.2",
    "C_LTA": "0.1",
    "C_VHD": "0.3",
    "C_VAD": "0.1",
    "C_HTB": "0.2",
    "C_PHB": "0.1",
}


@dataclasses.dataclass(frozen=True)
class ContractCase:
    """One scenario: overrides on :data:`FULL_ROW` and the rule ids it violates."""

    case_id: str
    overrides: dict[str, str | None]
    violated: tuple[str, ...]


_ODDS_COLUMNS = ("OddHome", "OddDraw", "OddAway", "MaxHome", "MaxDraw", "MaxAway")

CONTRACT_CASES: tuple[ContractCase, ...] = (
    ContractCase("valid_full", {}, ()),
    ContractCase("ht_home_gt_ft", {"HTHome": "3.0"}, ("ht_le_ft_home",)),
    ContractCase(
        "ht_away_gt_ft",
        {"HTHome": "0.0", "HTAway": "2.0", "HTResult": "A"},
        ("ht_le_ft_away",),
    ),
    ContractCase("ht_eq_ft", {"HTHome": "2.0", "HTAway": "1.0"}, ()),
    ContractCase("ft_result_wrong", {"FTResult": "A"}, ("ft_result_consistent",)),
    ContractCase(
        "ft_result_draw_wrong",
        {"FTHome": "1.0", "FTAway": "1.0", "FTResult": "H"},
        ("ft_result_consistent",),
    ),
    ContractCase("ht_result_wrong", {"HTResult": "D"}, ("ht_result_consistent",)),
    ContractCase("ht_result_missing_goals", {"HTHome": None, "HTAway": None, "HTResult": "H"}, ()),
    ContractCase("ht_partial", {"HTAway": None, "HTResult": "H"}, ()),
    ContractCase("sot_gt_shots_home", {"HomeTarget": "15.0"}, ("sot_le_shots_home",)),
    ContractCase("sot_gt_shots_away", {"AwayTarget": "10.0"}, ("sot_le_shots_away",)),
    ContractCase("sot_eq_shots", {"HomeTarget": "14.0", "AwayTarget": "9.0"}, ()),
    ContractCase("sot_one_side_missing", {"HomeShots": None, "HomeTarget": "30.0"}, ()),
    ContractCase("odd_below_one", {"OddDraw": "0.99"}, ("odds_min",)),
    ContractCase("odd_zero_max_missing", {"OddHome": "0.0", "MaxHome": None}, ("odds_min",)),
    ContractCase("max_odd_below_one", {"MaxAway": "0.5"}, ("odds_min", "max_odds_ge_avg")),
    ContractCase("odd_exactly_one", {"OddHome": "1.0", "MaxHome": "1.0"}, ()),
    ContractCase("max_below_avg", {"MaxHome": "1.6"}, ("max_odds_ge_avg",)),
    ContractCase("odds_all_missing", dict.fromkeys(_ODDS_COLUMNS), ()),
    ContractCase("date_before_2000", {"MatchDate": "1999-12-31"}, ("date_in_range",)),
    ContractCase("date_2000_01_01", {"MatchDate": "2000-01-01"}, ()),
    ContractCase("date_run_date", {"MatchDate": "{run_date}"}, ()),
    ContractCase("date_after_run_date", {"MatchDate": "{run_date+1}"}, ("date_in_range",)),
    ContractCase("ht_result_missing_with_goals", {"HTResult": None}, ()),
    ContractCase("avg_missing_max_present", {"OddHome": None}, ()),
    ContractCase(
        "draw_only_max_below_avg",
        {
            "OddHome": None,
            "OddAway": None,
            "MaxHome": None,
            "MaxAway": None,
            "MaxDraw": "3.5",
        },
        ("max_odds_ge_avg",),
    ),
    ContractCase(
        "multi_rule",
        {"FTHome": "0.0", "FTAway": "2.0"},
        ("ht_le_ft_home", "ft_result_consistent"),
    ),
)

CASE_IDS = [case.case_id for case in CONTRACT_CASES]


def materialize(case: ContractCase, run_date: dt.date) -> dict[str, str | None]:
    """Return the full source row of ``case``, with ``MatchDate`` placeholders resolved."""
    row: dict[str, str | None] = {**FULL_ROW, **case.overrides}
    row["MatchDate"] = (
        (row["MatchDate"] or "")
        .replace("{run_date}", run_date.isoformat())
        .replace("{run_date+1}", (run_date + dt.timedelta(days=1)).isoformat())
    )
    return row
