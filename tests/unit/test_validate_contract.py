"""Unit tests for contract integration in ``validate.partition`` (precedence and detail)."""

import json

import pytest

from football_stats.data_contract import CURRENT_CONTRACT, PRE_CONTRACT, find_violations
from tests.conftest import fixture_path
from tests.contract_cases import FULL_ROW
from tests.stages import RUN_DATE, partition_file, partition_rows
from tests.unit.test_validate_rows import only_excluded, outcomes


def _expected(name: str) -> dict[int, object]:
    data = json.loads(fixture_path(name).read_text("utf-8"))
    return {int(k): v for k, v in data.items()}


def test_contract_fixture_outcomes_match_expected() -> None:
    path = fixture_path("matches_contract.csv")
    assert dict(outcomes(partition_file(path))) == _expected("matches_contract.expected.json")
    pre = partition_file(path, contract=PRE_CONTRACT)
    assert dict(outcomes(pre)) == _expected("matches_contract.pre.expected.json")


def test_multi_rule_row_detail_lists_all_rules_in_contract_order() -> None:
    part = partition_rows([{**FULL_ROW, "FTHome": "0.0", "FTAway": "2.0"}])
    assert only_excluded(part) == {
        "reason": "contract_violation",
        "detail": "ht_le_ft_home, ft_result_consistent",
    }


def test_per_column_failure_takes_precedence_over_contract() -> None:
    part = partition_rows([{**FULL_ROW, "HomeShots": "-1", "FTResult": "A"}])
    assert only_excluded(part) == {"reason": "invalid_value", "detail": "HomeShots"}


def test_same_team_takes_precedence_over_contract() -> None:
    part = partition_rows(
        [{**FULL_ROW, "AwayTeam": " arsenal", "HomeTeam": "Arsenal", "FTResult": "A"}]
    )
    assert only_excluded(part) == {"reason": "same_team", "detail": "arsenal"}


def test_contract_checked_before_duplicates() -> None:
    part = partition_rows([{"FTResult": "D"}, {}])
    assert outcomes(part) == {
        1: {"reason": "contract_violation", "detail": "ft_result_consistent"},
        2: "loaded",
    }


def test_invalid_later_copy_is_contract_violation_not_duplicate() -> None:
    # partition_rows' own base row has no shots, so the rule would be skipped: spell rows out.
    part = partition_rows([{**FULL_ROW}, {**FULL_ROW, "AwayTarget": "10.0"}])
    assert outcomes(part) == {
        1: "loaded",
        2: {"reason": "contract_violation", "detail": "sot_le_shots_away"},
    }


@pytest.mark.parametrize(
    "name",
    [
        "matches_valid.csv",
        "matches_edge_cases.csv",
        "matches_real_sample.csv",
        "matches_contract.csv",
    ],
)
def test_accepted_rows_satisfy_contract(name: str) -> None:
    part = partition_file(fixture_path(name))
    assert find_violations(part.accepted, CURRENT_CONTRACT, RUN_DATE).height == 0
    assert part.accepted.height > 0
