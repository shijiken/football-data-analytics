"""Error types mapped one-to-one onto CLI exit codes (contracts/cli.md)."""

from __future__ import annotations


class FootballStatsError(Exception):
    """Base class for all handled pipeline failures."""

    exit_code: int = 1
    error: str = "unexpected_error"


class ConfigurationError(FootballStatsError):
    """A required environment variable is missing or invalid."""

    exit_code = 2
    error = "configuration_error"


class SourceUnavailableError(FootballStatsError):
    """The source file could not be downloaded, copied or read completely."""

    exit_code = 3
    error = "source_unavailable"


class MissingColumnsError(FootballStatsError):
    """The source header lacks one or more expected columns."""

    exit_code = 4
    error = "missing_columns"

    def __init__(self, missing: tuple[str, ...]) -> None:
        """Store the missing column names (in contract order) and build the message."""
        self.missing = missing
        super().__init__(f"Source file is missing expected columns: {', '.join(missing)}")


class MalformedSourceError(FootballStatsError):
    """The source file is structurally broken (encoding, field counts, duplicate header)."""

    exit_code = 4
    error = "malformed_source"


class ConcurrentRunError(FootballStatsError):
    """Another ingest run holds the advisory lock."""

    exit_code = 5
    error = "concurrent_run"


class VerificationFailedError(FootballStatsError):
    """``verify`` found differences between the source file and the database."""

    exit_code = 6
    error = "verification_failed"

    def __init__(self, message: str, checked: int, differences: list[dict[str, object]]) -> None:
        """Carry the verification report fields alongside the message."""
        super().__init__(message)
        self.checked = checked
        self.differences = differences


class LoadError(FootballStatsError):
    """The database was unreachable or a statement failed during the load."""

    exit_code = 7
    error = "database_error"


class AccountingError(FootballStatsError):
    """Internal invariant ``rows_read = accepted + excluded`` was violated."""

    exit_code = 8
    error = "accounting_error"


class ContractSchemaError(FootballStatsError):
    """The frame handed to the data contract does not match its declared columns or dtypes.

    A programming error (parser/contract mismatch), never bad data: it is not turned into
    exclusions.
    """

    exit_code = 9
    error = "contract_schema_error"


class ClockSkewError(FootballStatsError):
    """The database's UTC date is earlier than the run date (application/database clocks differ)."""

    exit_code = 10
    error = "clock_skew"
