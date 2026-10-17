"""Pin the pandera polars-backend behaviour the data contract relies on (research.md R2)."""

import pandera.errors
import pandera.polars as pa
import polars as pl
import pytest
from pandera.config import ValidationDepth, config_context
from pandera.polars import PolarsData


def _a_le_b(data: PolarsData) -> pl.LazyFrame:
    return data.lazyframe.select((pl.col("a") <= pl.col("b")).alias("check_output"))


def _schema() -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {"a": pa.Column(pl.Int16), "b": pa.Column(pl.Int16)},
        checks=[pa.Check(_a_le_b, name="a_le_b")],
        strict=False,
        coerce=False,
    )


def _frame() -> pl.DataFrame:
    return pl.DataFrame(
        {"a": [0, 1, 2, 3], "b": [1, 1, 1, 1]}, schema={"a": pl.Int16, "b": pl.Int16}
    )


def _failures(frame: pl.DataFrame) -> pl.DataFrame:
    with pytest.raises(pandera.errors.SchemaErrors) as info:
        _schema().validate(frame, lazy=True)
    failure_cases = info.value.failure_cases
    assert isinstance(failure_cases, pl.DataFrame)
    return failure_cases


def test_pandera_polars_backend_behaviour() -> None:
    # (1) failure_cases is a polars frame with the columns we map from
    cases = _failures(_frame())
    assert {"schema_context", "check", "index"} <= set(cases.columns)

    # (2) name, context and 0-based positions in the frame passed to validate
    assert set(cases["schema_context"]) == {"DataFrameSchema"}
    assert set(cases["check"]) == {"a_le_b"}
    assert sorted(cases["index"].to_list()) == [2, 3]
    sliced = _failures(_frame().slice(1))
    assert sorted(sliced["index"].to_list()) == [1, 2]

    # (3) the global switch disables validation; a nested forced context re-enables it
    with config_context(validation_enabled=False):
        _schema().validate(_frame(), lazy=True)
        with (
            config_context(
                validation_enabled=True, validation_depth=ValidationDepth.SCHEMA_AND_DATA
            ),
            pytest.raises(pandera.errors.SchemaErrors),
        ):
            _schema().validate(_frame(), lazy=True)

    # (4) a dtype mismatch is a Column-context failure with a null index
    wrong = _frame().with_columns(pl.col("a").cast(pl.Int64))
    with pytest.raises(pandera.errors.SchemaErrors) as info:
        _schema().validate(wrong, lazy=True)
    dtype_cases = info.value.failure_cases
    column_rows = dtype_cases.filter(pl.col("schema_context") == "Column")
    assert column_rows.height >= 1
    assert column_rows["index"].null_count() == column_rows.height
