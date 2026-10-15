"""The README must document the current data contract (FR-012)."""

from pathlib import Path

from football_stats.data_contract import CONTRACT_VERSION, CURRENT_CONTRACT

README = Path(__file__).resolve().parents[2] / "README.md"


def test_readme_documents_every_rule() -> None:
    text = README.read_text("utf-8")
    assert "Data contract" in text
    assert f"contract version {CONTRACT_VERSION}" in text.lower()
    for rule_id in CURRENT_CONTRACT.rule_ids:
        assert f"`{rule_id}`" in text, rule_id
