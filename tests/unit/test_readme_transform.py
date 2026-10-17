"""The README must document the analytics transforms and their measured indexes (feature 003)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
MARTS = REPO_ROOT / "transform" / "models" / "marts"


def _section(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    assert match is not None, f"missing section: {heading}"
    return match.group(1)


def _first_cells(section: str) -> list[str]:
    rows = [line for line in section.splitlines() if line.startswith("|")]
    return [row.split("|")[1].strip() for row in rows]


def test_readme_documents_transform() -> None:
    """Section, season rule, one measured row per index and key, rejected candidates, no TBD."""
    text = README.read_text("utf-8")
    transforms = _section(text, "Analytics transforms")
    for name in (
        "analytics_staging",
        "analytics_marts",
        "mart_team_form",
        "mart_league_season_home_advantage",
        "mart_league_season_goals",
        "mart_team_ratings",
    ):
        assert name in transforms, name
    assert "July" in transforms
    assert "YYYY/YY" in transforms

    measured = _section(text, "Measured numbers - analytics transforms")
    cells = _first_cells(measured)
    index_config = (MARTS / "mart_team_form.sql").read_text("utf-8")
    declared = re.findall(r'"columns":\s*\[([^\]]*)\]', index_config)
    assert declared, "mart_team_form.sql declares no index"
    for columns in declared:
        names = ", ".join(re.findall(r'"(\w+)"', columns))
        assert f"`mart_team_form ({names})`" in cells, names
    for mart in yaml.safe_load((MARTS / "_marts.yml").read_text("utf-8"))["models"]:
        assert f"`{mart['name']} primary key`" in cells, mart["name"]
    assert "`matches (match_date)`" in cells
    assert "`matches (league_code, match_date)`" in cells

    assert "TBD" not in text
    for block in re.split(r"^```", text, flags=re.MULTILINE)[0::2]:
        assert "CREATE INDEX" not in block, "the README must not suggest hand-made indexes"
    for block in re.split(r"^```", text, flags=re.MULTILINE)[1::2]:
        if "CREATE INDEX" in block:
            assert "ROLLBACK" in block
