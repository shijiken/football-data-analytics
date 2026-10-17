import polars as pl
import pytest

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
    assert counts["missing_result"] == 1
    assert counts["missing_field"] == 1
    assert sum(counts.values()) == part.excluded.height == 2
    assert part.accepted.height == 14
