# Code Review — Round 2

**Feature**: `003-dbt-transforms` | **Branch**: `003-dbt-transforms` | **Fix commit**: `9194421`
(responses in decisions.md "Code review round 1", C1-1..C1-6)

Verdict: APPROVE

Counts: 0 BLOCKER, 0 MAJOR, 0 MINOR

## Gate (run by the reviewer)

`scripts/check.sh` on `9194421`: **exit 0**. ruff format and check are clean, `mypy --strict` is clean,
193 unit tests and 160 integration tests pass. The smoke test passed in round 1, and none of the files
it depends on (Dockerfile, compose file, smoke script, profiles) changed in this commit.

## Verification of each fix

### C1-1 (MAJOR): recovery test now proves convergence. FIXED, verified by mutation
`tests/integration/test_transform_build.py:229-252`. Between the failing build and the rerun, the
test now does three things: it deletes `mart_team_form` rows, zeroes `mart_team_ratings.peak_elo`, and
drops `mart_league_season_goals`. It then asserts that the damage is visible (`UndefinedTable`, and both
damaged marts differ from the reference). After the rerun it asserts:
- the model set equals `EXPECTED_MODELS`;
- every model is `success` and every test is `pass`;
- the full snapshot equals the reference;
- `public` equals `p0`.

I mutated a scratch copy of HEAD (made with `git archive`, so the repository was not touched) and ran
the test against it:

| Mutation | Result |
|---|---|
| rerun skipped (`rerun = first`, so exit 0 and all-success statuses are inherited) | **FAILS**: `UndefinedTable: analytics_marts.mart_league_season_goals` at the final snapshot |
| partial rerun (`dbt build --exclude mart_team_ratings`) | **FAILS**: the model-set assertion reports that `mart_team_ratings` is missing |
| unmodified (gate) | passes |

The implementer also reported that a `dbt parse` substitute fails. Taken together, the test now fails
when the rerun is skipped, partial, or a no-op.

### C1-2 (MINOR): empty-database test. FIXED
The `continue` is gone. Every relation in `ANALYTICS_MODELS` must exist and hold 0 rows, and every
status must be `success` or `pass`.

### C1-3 (MINOR): explicit mypy cap. FIXED
`pyproject.toml` now has `"mypy>=1.19.1,<2"` with a comment naming the pathspec cause and I-2. The
only `uv.lock` change is the specifier in `requires-dev`, and the resolved version stays 1.19.1.

### C1-4 (MINOR): ad hoc PK measurements reproducible. FIXED
The README has a fenced block with both `BEGIN; … \gset; ALTER TABLE … DROP CONSTRAINT :"pk"; EXPLAIN
(ANALYZE, BUFFERS) …; ROLLBACK;` variants and their literal keys. Each one rolls back and none
contains `CREATE INDEX`, so it is consistent with the README test and the "nothing persists" claim.

### C1-5 (MINOR): team-form invariant and `is_home`. FIXED
`season_start_year` is aggregated and compared for both sides in
`assert_team_form_two_rows_per_match.sql`. `mart_team_form.is_home` has a `not_null` test, which brings
the node count to 76. The implementer chose the `not_null` option over a per-match `count(*) <> 2`,
and that choice is acceptable: once `is_home` cannot be null, every row is counted on exactly one side,
and the `n <> 1` checks plus the PK cover the rest. The README says 76 nodes / 69 tests, and records a
real-data `PASS=76` rerun next to the original `PASS=75` measurement. The plan, tasks and quickstart
still say 75. decisions.md declares those documents historical and the README authoritative, and I
accept that; it is not re-raised.

### C1-6 (MINOR): manifest typing. FIXED
`manifest()` returns `dict[str, Any]` and all four `type: ignore`s are removed. mypy `--strict` is clean.

## Rulings

All earlier rulings (I-1..I-6 ACCEPT) stand, and no new deviations were introduced. Nothing new was
found in the fix diff: it touches only tests, one YAML test entry, one singular test, the README,
pyproject and the lock specifier, with no model SQL changes.
