# Contract: Match Data Contract, version 1

**Feature**: `002-data-contract` | Decisions: [research.md](../research.md) R3, R6, R8 |
Code: `src/football_stats/data_contract.py` (the single source of truth; this page describes it)

A match row that passed feature 001's per-column checks must satisfy every rule below, or it is
excluded with reason `contract_violation` and a `detail` listing every violated rule id in the order
of this table, joined by `", "`. The database enforces the same rules with the constraint named in
the last column.

## Semantics

- A rule is one or more **terms**. A term is **skipped** when any of its inputs is missing (NULL).
- A row violates a rule iff at least one non-skipped term is false.
- Comparisons are inclusive (`<=`, `>=`). Goals and shots are integers; odds are decimal numbers
  (IEEE-754 double precision on both sides).
- **Run date** = the UTC date at which the ingest run started.

## Rules (contract order)

| # | Rule id | Description | Terms (inputs → condition) | DB constraint |
|---|---|---|---|---|
| 1 | `ht_le_ft_home` | Half-time home goals do not exceed full-time home goals | (`HTHome`, `FTHome`) → `HTHome <= FTHome` | `matches_contract_ht_le_ft_home` |
| 2 | `ht_le_ft_away` | Half-time away goals do not exceed full-time away goals | (`HTAway`, `FTAway`) → `HTAway <= FTAway` | `matches_contract_ht_le_ft_away` |
| 3 | `ft_result_consistent` | Full-time result matches the full-time score | (`FTHome`, `FTAway`, `FTResult`) → `FTResult` is `H` if home > away, `D` if equal, `A` if home < away | `matches_contract_ft_result_consistent` |
| 4 | `ht_result_consistent` | Half-time result matches the half-time score | (`HTHome`, `HTAway`, `HTResult`) → same as rule 3 | `matches_contract_ht_result_consistent` |
| 5 | `sot_le_shots_home` | Home shots on target do not exceed home shots | (`HomeShots`, `HomeTarget`) → `HomeTarget <= HomeShots` | `matches_contract_sot_le_shots_home` |
| 6 | `sot_le_shots_away` | Away shots on target do not exceed away shots | (`AwayShots`, `AwayTarget`) → `AwayTarget <= AwayShots` | `matches_contract_sot_le_shots_away` |
| 7 | `odds_min` | Every pre-match decimal odd is at least 1.0 | six terms, one per column `OddHome`, `OddDraw`, `OddAway`, `MaxHome`, `MaxDraw`, `MaxAway` → value `>= 1.0` | `matches_contract_odds_min` |
| 8 | `max_odds_ge_avg` | Each maximum odd is at least the corresponding average odd | (`OddHome`, `MaxHome`) → `MaxHome >= OddHome`; same for Draw, Away | `matches_contract_max_odds_ge_avg` |
| 9 | `date_in_range` | Match date is between 2000-01-01 and the run date, inclusive | (`MatchDate`) → `2000-01-01 <= MatchDate <= run date` | `matches_contract_date_in_range` (upper bound: the database transaction's UTC date, always ≥ the run date) |

Not covered by version 1 (spec Assumptions): Elo, form, corners, fouls, cards, over/under and
handicap odds, cluster columns; statistical plausibility (e.g. 15–0).

## Versioning

- `CONTRACT_VERSION` is an integer, recorded in every `ingest_runs` row and run report.
- Version 0 (`PRE_CONTRACT`) = no cross-field rules (runs made by feature 001; back-filled).
- Any rule added, removed or changed in meaning increments the version by 1 **and** ships with an
  Alembic revision that adds/drops/replaces the matching `matches_contract_<id>` constraint, the
  README table, and new rows in `tests/contract_cases.py`. The gate fails otherwise
  (`test_every_contract_rule_has_db_constraint`, `test_db_agrees_with_pipeline_on_contract_case`,
  `test_readme_documents_every_rule`).
- After a version change, re-running ingestion on the same source file converges the database to
  what a fresh run with the new version produces (FR-009).
