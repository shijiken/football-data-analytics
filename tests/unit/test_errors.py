from football_stats.errors import ClockSkewError, ContractSchemaError, FootballStatsError


def test_contract_schema_error_exit_code() -> None:
    assert issubclass(ContractSchemaError, FootballStatsError)
    assert ContractSchemaError.exit_code == 9
    assert ContractSchemaError.error == "contract_schema_error"


def test_clock_skew_error_exit_code() -> None:
    assert issubclass(ClockSkewError, FootballStatsError)
    assert ClockSkewError.exit_code == 10
    assert ClockSkewError.error == "clock_skew"
