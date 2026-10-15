import polars as pl
import pytest

from football_stats.data_contract import CURRENT_CONTRACT
from football_stats.errors import AccountingError
from football_stats.source_contract import ExclusionReason
from football_stats.validate import Partition
from tests.conftest import fixture_path
from tests.stages import partition_file


@pytest.mark.parametrize(
    ("name", "rows_read"),
    [
        ("matches_valid.csv", 12),
        ("matches_edge_cases.csv", 30),
        ("matches_real_sample.csv", 16),
        ("matches_contract.csv", 23),
        ("matches_header_only.csv", 0),
    ],
)
def test_partition_accounts_for_every_row(name: str, rows_read: int) -> None:
    part = partition_file(fixture_path(name))
    accepted = part.accepted["source_row_number"].to_list()
    excluded = part.excluded["source_row_number"].to_list()
    assert part.rows_read == rows_read
    assert set(accepted).isdisjoint(excluded)
    assert sorted(accepted + excluded) == list(range(1, rows_read + 1))


def test_partition_rejects_inconsistent_counts() -> None:
    frame = pl.DataFrame({"source_row_number": [1, 2]})
    with pytest.raises(AccountingError):
        Partition(accepted=frame, excluded=frame.clear(), rows_read=3)


def test_report_counts_per_reason() -> None:
    part = partition_file(fixture_path("matches_real_sample.csv"))
    counts = part.counts_by_reason()
    assert set(counts) == {reason.value for reason in ExclusionReason}
    assert len(counts) == 7
    assert counts["missing_result"] == 1
    assert counts["missing_field"] == 1
    assert sum(counts.values()) == part.excluded.height == 2
    assert part.accepted.height == 14


def test_violations_by_rule_counts_each_rule_once_per_row() -> None:
    part = partition_file(fixture_path("matches_contract.csv"))
    by_rule = part.violations_by_rule()
    assert list(by_rule) == list(CURRENT_CONTRACT.rule_ids)
    assert by_rule == {
        "ht_le_ft_home": 2,
        "ht_le_ft_away": 1,
        "ft_result_consistent": 3,
        "ht_result_consistent": 1,
        "sot_le_shots_home": 2,
        "sot_le_shots_away": 2,
        "odds_min": 2,
        "max_odds_ge_avg": 1,
        "date_in_range": 2,
    }
    assert part.counts_by_reason()["contract_violation"] == 15
    assert sum(by_rule.values()) == 16

    clean_part = partition_file(fixture_path("matches_valid.csv"))
    zeros = dict.fromkeys(CURRENT_CONTRACT.rule_ids, 0)
    assert clean_part.violations_by_rule() == zeros


@pytest.mark.parametrize(
    "name",
    [
        "matches_valid.csv",
        "matches_edge_cases.csv",
        "matches_real_sample.csv",
        "matches_contract.csv",
        "matches_header_only.csv",
    ],
)
def test_report_invariants_hold(name: str) -> None:
    part = partition_file(fixture_path(name))
    counts = part.counts_by_reason()
    violations = sum(part.violations_by_rule().values())
    assert counts["contract_violation"] <= violations
    assert (counts["contract_violation"] == 0) == (violations == 0)
    assert sum(counts.values()) == part.excluded.height
    assert part.accepted.height + part.excluded.height == part.rows_read
