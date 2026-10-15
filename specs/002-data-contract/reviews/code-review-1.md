# Code Review — Round 1

**Feature**: `002-data-contract` | **Branch**: `002-data-contract` | **Range**: `998bf19..93f4644`
(implementation `5be500f..93f4644`) | **Reviewer date**: 2026-10-15

Verdict: APPROVE

Counts: 0 BLOCKER, 0 MAJOR, 4 MINOR.

## Gate

`scripts/check.sh` was re-run by the reviewer: exit 0. ruff format and ruff check are clean, and so
is `mypy` (src + tests). There are 182 unit tests and 121 integration tests (real PostgreSQL 17.6
via testcontainers, fixed random seed), and all pass. The diff contains no `skip`, `xfail`,
`noqa` or `type: ignore`. The script uses `set -euo pipefail`, so any failing step fails the gate.

## Requirement → proof

| Req | Test(s) read | Strength |
|---|---|---|
| FR-001 | `test_contract_rule_ids_stable`, `test_schema_declares_rule_inputs_with_parser_dtypes` | Exact id tuple, term counts, dtypes, nullability and check names |
| FR-002 | `test_contract_case_outcome[*]` (27 cases), `test_contract_cases_cover_every_rule` | Exact detail string per case. Every rule has a single-rule case, and the skip-on-null cases are included |
| FR-003 | `test_contract_checked_before_duplicates`, `test_invalid_later_copy_is_contract_violation_not_duplicate`, `test_per_column_failure_takes_precedence_over_contract`, `test_same_team_takes_precedence_over_contract` | Precedence pinned in both directions |
| FR-004 | `test_multi_rule_row_detail_lists_all_rules_in_contract_order`, `test_contract_fixture_loads_only_consistent_rows` | Exact `{row: reason/detail}` equality with the expected JSON |
| FR-005 | `test_run_report_and_record_contract_metadata`, `test_violations_by_rule_counts_each_rule_once_per_row`, `test_report_invariants_hold[*]`, `test_cli_ingest_report_matches_schema` | Zero counts, contract order, stored row = report, the nine log lines |
| FR-006 | same + `test_upgrade_backfills_existing_runs` | Version 1 stored; old runs back-filled to 0 / `{}` |
| FR-007 | `test_db_agrees_with_pipeline_on_contract_case[*]` (set equality per row using a probe table), `test_db_constraints_agree_with_pipeline_on_fixture_rows[*]`, `test_contract_constraint_rejects_violating_update`, `test_every_contract_rule_has_db_constraint` | Strong: parity is checked as a set equality, not first-failure only |
| FR-008 | `test_upgrade_succeeds_with_violating_matches`, `test_upgrade_is_noop_at_head`, `test_downgrade_refused_with_contract_violation_rows` | Upgrade over 13 violating rows; new violator rejected; second upgrade leaves the catalog unchanged |
| FR-009 | `test_upgrade_then_rerun_equals_fresh_build[*]`, `test_rerun_removes_now_violating_matches`, `test_rerun_loads_rows_whose_exclusion_no_longer_applies`, `test_reload_after_loosened_contract_equals_fresh_build`, `test_orphan_team_and_league_removed`, `test_converged_rerun_is_noop`, `test_convergence_scoped_to_source_file` | Full content-snapshot equality against a fresh database; scoping proven |
| FR-010 | `test_failure_after_convergence_rolls_back` | Has a positive control: the same call without the bad input removes 13 rows, so the rollback is what restored the data, not the deletes never running |
| FR-011/012 | decisions.md "FR-011 real-data review", README "Measured numbers", `test_readme_documents_every_rule` | Numbers cross-checked below |
| SC-001 | `test_no_loaded_match_violates_contract`, `assert_contract_validates_read_only` | `VALIDATE CONSTRAINT` inside a rolled-back transaction; `convalidated=false` asserted after |
| SC-002 | `Partition.__post_init__`, `test_report_invariants_hold`, `test_db_reconciles_with_run_counts[matches_contract.csv]` | Holds |
| SC-003 | per-case parity test (real INSERT + probe) | Holds |
| SC-004 | `test_upgrade_then_rerun_equals_fresh_build` | Holds |
| SC-005/006 | T036 measurements (17.1 s) | Recorded, not estimated |

## Focus areas

**Convergence SQL (`load.py:153-212`)**
- **Ordering.** The SQL runs in this order: the upsert, the stale-match delete, the stale-exclusion
  delete, then the orphan team and league deletes, then the `ingest_runs` insert and the
  excluded-row upsert.
- **Upsert before delete is correct.** Take a match whose kept copy changes from row 3 (now a
  violation) to row 7 (its duplicate). The upsert rewrites `source_row_number` to 7 in place, so
  the delete's `(row number, natural key)` anti-join keeps it and `match_id` is preserved. If the
  delete ran first, the row would be deleted and re-inserted.
- **NOT VALID interaction.** The constraints are checked only on rewritten tuples. Every rewritten
  tuple is a staged row that passed the pipeline, and `SET` covers every non-key column. A violating
  row that is not rewritten is only ever deleted, and a DELETE does not evaluate a CHECK.
- **Scoping.** Both stale deletes are scoped by `source_sha256 = %s`, which is parameterized.
  `test_convergence_scoped_to_source_file` proves that matches from other files survive.
- **Orphan deletes.** These are global by design (R11, README trade-off row). They are safe because
  nothing references `teams`/`leagues` except `matches`. They run under the advisory lock inside
  the same transaction, and they use the existing `home/away_team_id` indexes and the leading
  `league_code` of `matches_natural_key`.
- **SQL injection.** Every value is bound. The f-strings in migrations and tests interpolate only
  module constants or catalog output.

**pandera cannot be silently disabled.**
- `config_context(validation_enabled=True, SCHEMA_AND_DATA)` is forced around `validate`.
- It is proven in-process by `test_find_violations_ignores_disabled_pandera_config` and through
  environment variables in a subprocess by `test_find_violations_ignores_pandera_env_disable`.
- The library behaviour is pinned by `test_pandera_backend.py`.
- A failure case that is not a rule (a dtype failure, nullability, a raising check, or a null
  index) raises `ContractSchemaError`. This is tested for a wrong dtype and a raising rule, and I
  probed it for a null required column. The one gap is a missing column; see finding 1.

**Clock skew and run date.**
- `run_date = started_at.astimezone(UTC).date()`, and a naive `started_at` is refused.
- `_check_clock` runs after the lock and before any write, in the same transaction whose `now()`
  the CHECK uses, so both see the same instant.
- A DB date later than `run_date` makes the database looser than the pipeline, which is harmless.
- `test_db_clock_behind_run_date_fails_before_writes` asserts both dates in the message and an
  unchanged snapshot.
- The UTC conversion is covered by the +02:00 boundary test.

**README T036 numbers vs decisions.md.** Everything is consistent:
- Totals: 238,859 = 237,473 + 1,386, and 1,386 = 1 + 4 + 1,381. 238,854 − 1,381 = 237,473.
- The six detail combinations (1,356 / 10 / 7 / 4 / 3 / 1) add to 1,381. They reproduce the
  per-rule counts 1,357 / 13 / 7 / 8 and the "4 rows break two rules" claim.
- All shares are correctly rounded.
- The re-run wall times in the README (13.1 s, 10.5 s, 9.3 s) are not repeated in decisions.md.
  That is acceptable; it is a single source, not a conflict.

## Rulings on implementer deviations

- **I-1: ACCEPT.** The guard is stricter than specified: it scans every fixture, including the
  malformed ones. Moving it into a single owning file removes the ambiguity.
- **I-2: ACCEPT.** No contract expression reads team ids, and `LIKE … INCLUDING DEFAULTS INCLUDING
  IDENTITY` copies no FK or CHECK, so literal ids lose no coverage.
- **I-3: ACCEPT.**
  - Without the edit, the modified row (FT 0-3, HT 1-0) breaks `ht_le_ft_home` and is now
    correctly excluded.
  - The test's purpose is preserved: the same 12 `match_id`s, `(0, 12, 0)`, the edited FT tuple
    `(0, 3, 'A')` asserted, and one `source_sha256`.
  - The new `assert fields[14:17] == ["1.0", "0.0", "H"]` guard even fails loudly if the fixture
    drifts.
  - No assertion was weakened. The `matches_edge_cases.csv` repair (Twente/Ajax HT 1-0 H → 0-1 A
    on a 0-2 A full time) is the same kind of change. Those rows exist to test team-key
    normalisation and duplicates, not half-time data.
- **I-4: ACCEPT, with hardening (finding 2).**
  - Accounting cannot break: `__post_init__` still enforces read = accepted + excluded whatever the
    defaults.
  - Per-rule counts, however, can silently disagree with the exclusions. I confirmed
    `Partition(accepted=∅, excluded=[1 contract_violation row], rows_read=1)` builds and reports
    `counts_by_reason()["contract_violation"] == 1` with `violations_by_rule() == {}`.
  - The only production constructor (`validate.partition`) always passes both fields, so this is
    latent, not live.
- **I-5: ACCEPT.** Bumping a 0-0 draw's away goals would rightly be refused by
  `matches_contract_ft_result_consistent`. Row 9 (Everton 4-0, HT 1-0 H) stays consistent at 4-1
  H. The expected difference has the same shape (`ft_away_goals`, `"0"` → `"1"`), so the test is
  just as strong.
- **I-6: ACCEPT.** `fileConfig` in `env.py` replacing the root handlers is a real reason. The
  assertion is still an exact message match.
- **I-7: ACCEPT** for quickstart.md. The README has the same trap and is not fixed (finding 4).
- **I-8: UPHOLD, in part.**
  - The observation is accurate, and CI is safe because it starts from a clean runner and builds
    the image first.
  - But `README.md:87` states that `smoke_compose.sh` "builds the image", which it does not, and T037's
    first green run was false evidence. A smoke test that can pass against stale code on the
    developer's machine is a gate-integrity defect, even if a small one.
  - Root cause: `migrate` and `ingest` both `build: .` into two separately tagged images, so one
    can be rebuilt while the other goes stale. That is exactly how a plain `docker compose build`
    left `ingest` at feature 001.
  - The one-command principle still holds for a fresh clone, because Compose builds missing images
    on `run`/`up`. Even the stale case failed safely (exit 7, full rollback), so this is MINOR.
    See finding 3.

## Findings

1. **MINOR: `src/football_stats/data_contract.py:227-228`. A missing contract input column escapes
   as a raw polars error.**
   - Problem: `frame.select(inputs)` runs before pandera. When a declared input is absent, it raises
     `polars.exceptions.ColumnNotFoundError`, which I confirmed by probing. The CLI therefore
     reports exit 1 `unexpected_error`, not exit 9 `contract_schema_error`.
   - This contradicts the `find_violations` docstring ("does not match the contract's declared
     columns") and `contracts/cli.md` exit 9. The failure is still loud and happens before any
     write, so no data is at risk.
   - Required change: compute the declared inputs (plus `source_row_number`) that are missing from
     `frame.columns` and raise `ContractSchemaError` naming them before the `select`. Add a unit
     test, `test_find_violations_missing_column_raises_contract_schema_error`.

2. **MINOR: `src/football_stats/validate.py:61-70`. `Partition` does not check that `violations`
   agrees with `excluded` (I-4).**
   - Problem: because of the defaults, a caller can build a `Partition` whose `contract_violation`
     exclusions have no matching `violations` rows. The per-rule report and `ingest_runs.violations_by_rule`
     would then silently under-count (I demonstrated `1` vs `{}`).
   - Required change: extend `__post_init__` (raising `AccountingError`, not `assert`) to require:
     - the set of `source_row_number`s with reason `contract_violation` in `excluded` equals the
       distinct `source_row_number`s in `violations`;
     - every `violations.rule_id` is in `rule_ids`.

     Both hold trivially for feature 001's direct construction (no contract rows, empty frame), so
     the I-4 constraint is respected. Add one unit test that builds the inconsistent `Partition`
     and expects `AccountingError`.

3. **MINOR: `scripts/smoke_compose.sh:35` / `docker-compose.yml:17-38` / `README.md:87`. The smoke
   test can pass on a stale image (I-8).**
   - Problem: the script never builds, although the README says it does. Locally it reuses whatever
     `football_smoke-migrate` / `football_smoke-ingest` images exist, which can be different
     builds of the same Dockerfile.
   - Required change (either option):
     - (a) Add `"${COMPOSE[@]}" --profile ingest build` before `db up`, so the README claim
       becomes true.
     - (b) Give both services one shared `image:` tag (e.g. `football-stats:local`) with `build: .`
       on both, so they can never diverge, and build it in the script.

     Option (a) is the minimum. This lifts the T037 "do not edit the script" restriction for this
     single line.

4. **MINOR: `README.md:50-79`. The "Run it" section was not updated for contract version 1.**
   - Problems:
     - The sample report still shows `rows_loaded 238854`, `rows_excluded 5`, with no
       `contract_version` or `violations_by_rule`. A run now gives 237,473 / 1,386.
     - Exit codes and report schemas still link only to `specs/001-…/contracts/cli.md`, which has
       no exit 9/10 and none of the new fields.
     - There is no note that an existing checkout must rebuild before running. As I-7 showed, the
       documented `docker compose run --rm ingest` otherwise runs the feature-001 image and fails
       with exit 7.
   - The constitution requires that "the README MUST stay current with … run instructions".
   - Required change:
     - Update the sample JSON to the v1 numbers and fields.
     - Link `specs/002-data-contract/contracts/cli.md`.
     - Add one line: "After pulling a new version, rebuild first:
       `docker compose --profile ingest build` (or `docker compose run --rm --build ingest`)."

## Notes (no action required)

- The stale-match delete scans `matches` sequentially on `source_sha256`, as acknowledged in P1-9.
  T036 measured 13.1 s wall time for the converging re-run, well inside SC-005.
- The `date_in_range` CHECK uses `now()`, which is not immutable. This was accepted at plan time
  (R8) together with the clock-skew guard and the `NOT VALID`/never-validate policy; it is not
  re-raised.
