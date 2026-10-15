# Research: Match Data Contract

**Feature**: `002-data-contract` | **Date**: 2026-10-15 | **Plan**: [plan.md](./plan.md)

Every technology choice and every non-obvious design rule is recorded here as
**Decision / Rationale / Alternatives rejected**. Decisions of feature 001 are cited as
"001 R#" ([001 research.md](../001-match-data-ingestion/research.md)).

Evidence for this document was gathered on 2026-10-15:
- **pandera probe**: a scratch copy of the project (`pyproject.toml` + `uv.lock`) with
  `uv add "pandera[polars]"`, plus small probe scripts run against pandera 0.34.1 and polars 1.44.2
  (the locked polars). Nothing was added to the repository.
- **PostgreSQL probe**: a throwaway `postgres:17.6-alpine` container (the project's pinned image).
- **Real-data profile**: the pinned `Matches.csv` (sha256 `ef224cf2…4221`, copied out of the local
  `rawdata` Docker volume left by feature 001's T053 run, hash re-verified) run through the
  project's own `extract.read_source → clean.parse_frame → clean.add_team_keys →
  validate.partition`, then each proposed rule evaluated with a polars expression. The profile
  scripts lived in the scratchpad, not the repository. These numbers are planning estimates;
  FR-011/SC-006 numbers must come from a real pipeline run (task T036).

---

## R1. Validation library: pandera 0.34.x with the polars backend

**Facts (measured)**

| Item | Value |
|---|---|
| Resolved version | `pandera==0.34.1` (uploaded 2026-10-06) with extra `polars` |
| New transitive packages | `pydantic` 2.14.1, `pydantic-core`, `typeguard` 4.6.0, `typing-inspect`, `typing-inspection`, `annotated-types`, `mypy-extensions` (no pandas, no numpy) |
| polars compatibility | resolves against the locked `polars 1.44.2` (project range `>=1.30,<2`) |
| Typing | ships `py.typed`; a probe module using `pandera.polars.DataFrameSchema`, `Column`, `Check`, `PolarsData` and `pandera.config.config_context` passes `mypy --strict` with no ignores |
| `import pandera.polars` | does not import pandas or numpy |

**Decision**: Add `pandera[polars]>=0.34.1,<0.35` to `[project].dependencies` (locked by
`uv.lock`). Import it only as `import pandera.polars as pa` in `src/football_stats/data_contract.py`.

**Rationale**: The spec (Assumptions) and PLAN.md mandate pandera as the declarative contract. The
polars backend works on the frames the pipeline already has (001 R3), so no pandas conversion or
second DataFrame library enters the project. The upper bound `<0.35` is deliberate: pandera is
0.x and minor releases have changed backend behaviour before; the behaviours this design relies on
are pinned by a unit test (`test_pandera_polars_backend_behaviour`, R2) so an upgrade that changes
them fails the gate instead of silently changing which rows are excluded.

**Alternatives rejected**: Great Expectations (report-oriented, heavy, no row-level reason
mapping); pandera with the pandas backend (converts 239k×97 frames to pandas, adds pandas and numpy
for nothing); hand-written polars rules without pandera (contradicts the user-mandated technology,
and loses the declarative schema object that documents column dtypes and rules in one place).

## R2. How the contract is evaluated: pandera DataFrame-level checks, failure cases → violations

**Facts (measured on pandera 0.34.1)**

1. `DataFrameSchema.validate(df, lazy=True)` raises `SchemaErrors` whose `failure_cases` is a
   `pl.DataFrame` with columns `failure_case, schema_context, column, check, check_number, index`.
2. For a DataFrame-level `pa.Check(fn, name="<id>")` (no `error=`), the `check` column holds the
   name exactly; `schema_context` is `"DataFrameSchema"`; `index` is the **0-based row position in
   the frame passed to `validate`** (verified on a sliced frame: positions, not original labels).
   With `error="…"` given, `check` holds the error text instead, so we never pass `error=`.
3. A check function `fn(data: PolarsData) -> pl.LazyFrame` returning one boolean column is
   evaluated **vectorized** (one polars expression over the whole frame). 240,000 rows, every row
   failing two checks: 0.53 s for `validate` including building failure cases (worst case). The
   real data has ~1.4k failing rows (R16).
4. A check output of `null` is treated as a pass. Column-level built-in checks
   (`Check.ge`) also ignore nulls. **We do not rely on either**: every rule expression is
   null-guarded explicitly (R3), so its output is never null.
5. `pandera.config.config_context(validation_enabled=False)` — and the env var
   `PANDERA_VALIDATION_ENABLED=False` read at import — make `validate` return **without checking
   anything**. Wrapping the call in `config_context(validation_enabled=True,
   validation_depth=ValidationDepth.SCHEMA_AND_DATA)` overrides both (verified with the env var
   set before import).
6. A column dtype mismatch is reported in the same `failure_cases` with
   `schema_context="Column"`, `check="dtype('Int16')"`, `index=null`.
7. A check whose expression raises (probe: a condition reading a column that is not in the frame,
   `ColumnNotFoundError`) does **not** propagate: it becomes one failure case with
   `schema_context="DataFrameSchema"`, `check=<name>`, `index=null`, and the exception text in
   `failure_case`.
8. Failure cases are complete: 50,000 failing rows give 50,000 failure cases with 50,000 distinct
   indices (no cap or sampling).

**Decision**:
- `data_contract.build_schema(contract, run_date) -> pa.DataFrameSchema` builds a schema with
  one `pa.Column(<dtype>, nullable=<kind is optional>)` per rule input column (dtype derived from
  `source_contract.COLUMN_SPECS` parser: `COUNT→pl.Int16`, `DECIMAL→pl.Float64`,
  `RESULT→pl.String`, `DATE→pl.Date`), `strict=False`, `coerce=False`, and **one DataFrame-level
  `pa.Check` per rule**, `name=rule.rule_id`, whose function returns
  `data.lazyframe.select(rule.passes(run_date).alias("check_output"))`.
- `data_contract.find_violations(frame, contract, run_date) -> pl.DataFrame` selects
  `source_row_number` + the input columns, runs `validate(lazy=True)` inside the forced
  `config_context` above, and converts `failure_cases` into a long frame
  `(source_row_number: UInt32, rule_id: String)` by joining `index` to the row position, sorted by
  `source_row_number` then contract rule order. An empty contract or an empty frame returns an
  empty frame without calling pandera.
- Any failure case that is **not** (`schema_context == "DataFrameSchema"`, `check` ∈ rule ids,
  `index` not null) — a dtype mismatch, a missing column, a check that raised — is a programming
  error, not bad data: raise `ContractSchemaError` (exit 9, R14). It is never turned into
  exclusions, because that would exclude every row for a bug.
- The probed behaviours 1–2, 5 and 6 are pinned by
  `tests/unit/test_pandera_backend.py::test_pandera_polars_backend_behaviour`. Our own guarantees
  that rest on them are tested on `find_violations` itself (plan review P1-4):
  `test_find_violations_ignores_pandera_env_disable` (a subprocess, no network, started with
  `PANDERA_VALIDATION_ENABLED=False` and `PANDERA_VALIDATION_DEPTH=SCHEMA_ONLY` set **before**
  import, still reports a known violation; in-process tests cannot cover this because pandera reads
  the variables at import), `test_find_violations_is_complete_for_many_failures` (fact 8 through our
  mapping: 50,000 violating rows → 50,000 violation rows) and
  `test_find_violations_raising_rule_raises_contract_schema_error` (fact 7 → `ContractSchemaError`).

**Rationale**: pandera does the evaluation (it is not decorative): the schema object is the
contract, `validate(lazy=True)` collects every failure of every rule in one pass, and the failure
cases carry the row position needed for per-row reporting. DataFrame-level checks are needed
anyway for cross-column rules; using them for all rules (even `odds_min`, which spans six columns)
gives exactly one failure case per (row, rule), keyed by the stable rule id, so mapping needs no
string parsing of column/check combinations. Forcing the config context closes a real hole:
without it an environment variable could disable the contract silently (Principle I).

**Alternatives rejected**:
- *Column-level checks for single-column rules* (`pa.Column(..., checks=pa.Check.ge(1.0))`):
  `odds_min` would produce six failure cases per row under check names like
  `greater_than_or_equal_to(1.0)` that must be mapped back to one rule id; mixing two mappings for
  no benefit.
- *Element-wise checks* (`element_wise=True`): a Python call per row (239k × 9) — slow and
  pointless when every rule is a vectorized polars expression.
- *`DataFrameModel` class API*: `date_in_range` needs the run date at evaluation time; a schema
  built by a function taking `run_date` is simpler than class-level parametrization.
- *Evaluate the rule expressions directly with `frame.select(...)` and keep pandera only as a
  post-hoc assertion on accepted rows*: runs every rule twice and makes pandera a decoration; the
  failure-case route is fast enough (fact 3).
- *Trusting pandera's null-as-pass behaviour* instead of explicit guards: undocumented-looking
  backend detail; the explicit guard is also what keeps polars and SQL semantics identical (R3).

## R3. Rule semantics: terms, null tolerance, exact definitions

**Decision**: A rule is a conjunction of **terms**. Each term has its own input columns and a
condition; a term is **skipped** (treated as satisfied) when any of its inputs is null; the rule is
violated iff at least one non-skipped term is false. In polars a term is
`any_horizontal(input.is_null() for input in inputs) | condition`, and the rule is
`all_horizontal(terms)`; the output is never null. This is FR-002's "applied only when all of its
inputs are present", applied per comparison. For the two odds rules the spec text is per value
("**every** pre-match decimal odd ≥ 1.0", "**each** maximum odd ≥ the corresponding average odd"),
so their terms are per odd / per outcome.

| Rule id (contract order) | Terms (inputs → condition) | SQL CHECK (constraint `matches_contract_<id>`) |
|---|---|---|
| `ht_le_ft_home` | (`ht_home_goals`, `ft_home_goals`) → `ht_home_goals <= ft_home_goals` | `ht_home_goals <= ft_home_goals` |
| `ht_le_ft_away` | (`ht_away_goals`, `ft_away_goals`) → `ht_away_goals <= ft_away_goals` | `ht_away_goals <= ft_away_goals` |
| `ft_result_consistent` | (`ft_home_goals`, `ft_away_goals`, `ft_result`) → `ft_result == ("H" if h > a else "D" if h == a else "A")` | `ft_result = CASE WHEN ft_home_goals > ft_away_goals THEN 'H' WHEN ft_home_goals = ft_away_goals THEN 'D' WHEN ft_home_goals < ft_away_goals THEN 'A' END` |
| `ht_result_consistent` | (`ht_home_goals`, `ht_away_goals`, `ht_result`) → same with HT columns | same with HT columns |
| `sot_le_shots_home` | (`home_shots`, `home_shots_on_target`) → `home_shots_on_target <= home_shots` | `home_shots_on_target <= home_shots` |
| `sot_le_shots_away` | (`away_shots`, `away_shots_on_target`) → `away_shots_on_target <= away_shots` | `away_shots_on_target <= away_shots` |
| `odds_min` | six terms, one per column `odd_home`, `odd_draw`, `odd_away`, `max_odd_home`, `max_odd_draw`, `max_odd_away` → `<col> >= 1.0` | `odd_home >= 1.0 AND odd_draw >= 1.0 AND odd_away >= 1.0 AND max_odd_home >= 1.0 AND max_odd_draw >= 1.0 AND max_odd_away >= 1.0` |
| `max_odds_ge_avg` | three terms: (`odd_home`, `max_odd_home`) → `max_odd_home >= odd_home`; same for draw, away | `max_odd_home >= odd_home AND max_odd_draw >= odd_draw AND max_odd_away >= odd_away` |
| `date_in_range` | (`match_date`) → `2000-01-01 <= match_date <= run_date` | `match_date >= DATE '2000-01-01' AND match_date <= (now() AT TIME ZONE 'UTC')::date` |

Why the SQL is null-equivalent: PostgreSQL accepts a CHECK whose result is NULL. Each comparison
with a NULL input is NULL; `AND` over three-valued logic is FALSE iff some operand is FALSE, so the
constraint rejects a row iff some term with all inputs present is false — exactly the polars
semantics. The CASE uses an explicit `WHEN … < … THEN 'A'` (no `ELSE`), so missing goals give
NULL (skip), never `'A'`. Verified on the PostgreSQL probe: `(NULL, 0.9)` rejected by the odds
rule, `(NULL, 1.0)` accepted.

Floating point parity: decimals are parsed once in polars (`Float64`) and sent to PostgreSQL by
`write_csv` (shortest round-trip text) into `double precision`, so both sides compare the same
IEEE-754 values; boundary cases `max == avg` and `odd == 1.0` are in the shared parity case table
(R13).

**Rationale**: One semantic (term skipped iff an input is missing) explains every rule, matches the
spec wording, and is identical in polars and SQL, which is what makes constraint parity provable.
Per-term skipping for the odds rules is required for parity: with whole-rule skipping, a row with
`odd_home = 0.0` and no maximum odds would be accepted by the pipeline and rejected by the
database's `AND` constraint, aborting the whole run (FR-007). The real data contains such zero odds
(R16).

**Alternatives rejected**: whole-rule skipping when any of six odds is missing (parity break above;
would also let most 0.0 odds through, since maximum odds are absent for 27k rows); `ELSE 'A'` in
the CASE (rejects rows with missing HT goals but present `HTResult` — none exist today, but the
pipeline skips them, so it would be a latent parity break); one constraint per odds column
(`odds_min` would then map to six constraints; one rule ↔ one constraint keeps the parity test a
set equality).

## R4. Where the contract sits in the precedence, and the `detail` format

**Decision**: Feature 001's fixed precedence (001 R6) gains one reason, evaluated on rows that
passed reasons 1–5 and **before** `duplicate`:

| # | Code | Detail |
|---|---|---|
| 1–5 | `missing_field`, `invalid_date`, `missing_result`, `invalid_value`, `same_team` | unchanged (001 R6) |
| 6 | `contract_violation` | every violated rule id, in **contract order**, joined by `", "` — e.g. `ht_le_ft_home, ft_result_consistent` |
| 7 | `duplicate` | unchanged: `duplicate of source row N`, N = lowest `source_row_number` among rows that passed 1–6 |

`ExclusionReason` gets `CONTRACT_VIOLATION = "contract_violation"` between `SAME_TEAM` and
`DUPLICATE` (the enum order is the precedence order and is tested). `validate.partition` computes
reasons 1–5 as today, calls `data_contract.find_violations` on the candidate rows (reason still
null), assigns reason 6 to rows with ≥1 violation, and runs duplicate detection on the rest.

Determinism of `detail`: the long violation frame is sorted by (`source_row_number`, rule position
in `contract.rules`), then grouped with `maintain_order=True` and joined. The result depends only on
the row's values and the contract, never on pandera's failure-case order.

**Rationale**: Exactly FR-003 and both spec edge cases: contract rules only see rows that parsed
(a row failing per-column checks keeps its 001 reason), and a valid later copy of a match is never
lost because an earlier copy was invalid (the invalid copy is excluded first, so the valid one is
the first remaining row for the key). One reason per row keeps `sum(excluded_by_reason) ==
rows_excluded` (001 R6); listing every rule in `detail` satisfies FR-004. Contract order (not
alphabetical) mirrors 001's "columns in contract order" detail style.

**Alternatives rejected**: contract after duplicate detection (a broken first copy would hide a
valid second copy — the edge case forbids it); contract on all rows including those failing
per-column checks (rules would read nulls left by failed parsers and report noise; spec edge case
says 001 reason wins); one exclusion row per violated rule (breaks the one-row-one-reason accounting
and the `(source_sha256, source_row_number)` key).

## R5. The run date is injected, never read inside pure code

**Decision**: `data_contract` and `validate` take `run_date: dt.date` as a required keyword
argument; they never read a clock. `pipeline.run(settings, source_file=None, *, contract=
CURRENT_CONTRACT, started_at=None)` uses `started_at` (default `dt.datetime.now(dt.UTC)`, must be
timezone-aware) for both `ingest_runs.started_at` and `run_date = started_at.astimezone(dt.UTC).date()`.
`run_date` also travels to the loader in `RunMeta.run_date` for the clock check of R8. Unit tests
use the constant `RUN_DATE = dt.date(2023, 6, 30)` from `tests/stages.py`; integration tests that
ingest `matches_contract.csv` or test a date boundary pass `started_at=CONTRACT_STARTED_AT =
dt.datetime(2023, 6, 30, 12, 0, tzinfo=dt.UTC)`. Both lie after every fixture match date (latest
2022-02-07) and in the past, so no contract outcome depends on the real clock (plan review P1-2,
P2-1). Ingests that omit `started_at` (including feature-001 tests) still use `now()`; they are
deterministic only because every fixture date is in the past (P2-2).

**Rationale**: Spec: "run date is the date the run starts, in UTC". Injecting it makes the only
time-dependent rule deterministic in tests (planner rule), and using the same instant for the run
record keeps the report honest. The fixed test dates lie years in the past, so the database's own
`now()`-based bound (R8) is never stricter than the pipeline's in tests, even on a Docker VM whose
clock has drifted. In production the two clocks can differ; R8 adds an explicit check for that.

**Alternatives rejected**: `date.today()` inside the rule (untestable, local time zone);
a `RUN_DATE` environment variable (configuration for a test concern; production must not be able
to fake the date); freezegun-style clock patching (a dependency to hide what injection makes explicit).

## R6. Contract identity and versioning

**Decision**: `src/football_stats/data_contract.py` is the single place that defines the contract
(FR-001): `CONTRACT_VERSION = 1`, a frozen `DataContract(version: int, rules: tuple[ContractRule,
...])`, `ContractRule(rule_id: str, description: str, terms: tuple[Term, ...])` with property
`inputs` (union of term inputs, first-seen order), `Term(inputs: tuple[str, ...], condition:
Callable[[dt.date], pl.Expr])`, and `CURRENT_CONTRACT = DataContract(1, RULES)`. Version policy:
the version is a positive integer, incremented by 1 whenever a rule is added, removed, or its
meaning changes; every increment ships with a migration that keeps the database constraints in
parity (R8), enforced by the parity tests. `PRE_CONTRACT = DataContract(0, ())` names version 0:
"feature 001 per-column rules only" — the value back-filled into existing `ingest_runs` rows (R7)
and used by tests to reproduce a pre-contract database (R13).

**Rationale**: An integer is the simplest version that answers "which rules produced this run"
(US4). Semantic versions suggest compatibility promises a data contract cannot make: any rule
change can change which rows load. Keeping `PRE_CONTRACT` in the source (two lines) gives version 0
a definition instead of a magic number.

**Alternatives rejected**: semver strings (no consumer needs major/minor); a hash of the rule
definitions (not human-readable, changes on refactors that keep semantics); version stored only in
the README (FR-006 requires it per run).

## R7. Run record and report: `contract_version`, `violations_by_rule`, convergence counters

**Decision**: Migration `0002_contract_run_metadata` adds to `ingest_runs`:

| Column | Type | Rule |
|---|---|---|
| `contract_version` | `integer NOT NULL` | `CHECK (contract_version >= 0)`; existing rows back-filled `0` |
| `violations_by_rule` | `jsonb NOT NULL` | `CHECK (jsonb_typeof(violations_by_rule) = 'object')`; `{rule_id: count}` with **every** rule id of that contract version, zeros included; existing rows back-filled `{}` |
| `matches_removed` | `integer NOT NULL` | `CHECK (>= 0)`; matches of this source file deleted by convergence (R10); back-filled `0` |
| `excluded_rows_removed` | `integer NOT NULL` | `CHECK (>= 0)`; exclusion records of this file deleted by convergence; back-filled `0` |

Back-fill uses `ADD COLUMN … NOT NULL DEFAULT <v>` followed by `ALTER COLUMN … DROP DEFAULT`, so
old rows get the value and the pipeline must always write all four explicitly. The excluded-row
`reason` CHECK (`excluded_rows_reason_check`, the auto-generated name confirmed on the probe) is
dropped and re-created with `'contract_violation'` added; `excluded_by_reason` now always has all
seven codes.

The stdout report (`contracts/run-report.schema.json`, this feature) adds `contract_version`,
`violations_by_rule` (required keys = the nine rule ids), `matches_removed`,
`excluded_rows_removed`, and `contract_violation` in `excluded_by_reason`. Counting: a row violating
k rules adds 1 to each of those k rules and 1 to `excluded_by_reason.contract_violation`, so
`excluded_by_reason.contract_violation == number of rows with ≥ 1 violation <=
sum(violations_by_rule)`. Every per-rule count is also logged at INFO (`contract rule=<id>
violations=<n>`, zeros included).

**Rationale**: FR-005/FR-006/US4. A jsonb object mirrors the existing `excluded_by_reason` column
(one style for both), keeps the schema stable when a future contract version adds rules (the rule
set is data, not columns), and is still queryable (`violations_by_rule->>'odds_min'`). Recording
convergence deletions keeps Principle I honest: rows leaving `matches` are counted per run, not just
logged.

**Alternatives rejected**: one integer column per rule (a migration for every contract change
beyond the constraint itself; history rows of older versions would need NULL columns); a child table
`ingest_run_violations(run_id, rule_id, count)` (more joins for a nine-key map; nothing queries it
relationally); nullable `contract_version` meaning "pre-contract" (two meanings for "unknown" vs
"none"; version 0 is explicit); no convergence counters (deletions would be invisible in the run
history).

## R8. Database enforcement: named CHECK constraints added `NOT VALID`

**Facts (PostgreSQL 17.6 probe)**:
- `ALTER TABLE … ADD CONSTRAINT … CHECK (…) NOT VALID` succeeds on a table containing violating
  rows; `pg_constraint.convalidated = false`; `pg_get_constraintdef` ends in `NOT VALID`.
- The constraint is enforced on every new tuple: a new violating INSERT is rejected; **an UPDATE of
  an old violating row that changes only an unrelated column is also rejected** (the whole new
  tuple is checked); an UPDATE that rewrites the row to valid values succeeds.
- A CHECK may call `now()`; `(now() AT TIME ZONE 'UTC')::date` gives the UTC date regardless of the
  session `TimeZone` (checked with `America/Los_Angeles`).
- `ALTER TABLE … VALIDATE CONSTRAINT` later scans existing rows and flips `convalidated`.

**Decision**: Migration `0003_contract_constraints` (after `0002_contract_run_metadata`, see R18) adds nine constraints to `matches`, one per rule,
named `matches_contract_<rule_id>`, with the SQL of R3, all `NOT VALID`. They are never validated by
the migration or the application. A fresh database gets the same `NOT VALID` constraints (on an empty
table that is equivalent), so the catalog is identical however the database was reached. Nobody
validates them by hand: a committed `VALIDATE CONSTRAINT` changes the catalog (`convalidated`, the
`NOT VALID` suffix), which is a schema change outside Alembic (constitution, Technical Constraints) and
would make that database differ from `alembic upgrade head`. If validation is wanted later (e.g. once
every source file has been converged), it ships as a new Alembic revision. The only `VALIDATE` in
this feature is a **read-only proof**: quickstart §2 and the SC-001 tests run it inside
`BEGIN … ROLLBACK`, so the catalog is unchanged afterwards (plan review P1-1).

`date_in_range` in SQL uses the transaction's UTC date. The pipeline's `run_date` is taken before
the load transaction starts, so **when the application and the database share one clock** the
database bound is ≥ the pipeline bound and the database can never reject a row the pipeline
accepted (the parity direction that matters). The two clocks are not guaranteed to agree (a Docker
Desktop VM drifts after sleep; a managed database is another host; `pipeline.run(started_at=...)`
accepts any instant). Therefore `load_run`, right after taking the advisory lock and **before any
write**, runs `SELECT (now() AT TIME ZONE 'UTC')::date` and, if that date is earlier than
`RunMeta.run_date`, raises `ClockSkewError` (exit 10, `clock_skew`, message naming both dates)
instead of letting a `matches_contract_date_in_range` CheckViolation surface as an anonymous
`LoadError` halfway through the load. Tested by `test_db_clock_behind_run_date_fails_before_writes`.
A row valid when inserted stays valid forever, because the bound only moves forward; this is why a `now()`-based
CHECK is safe for dump/restore here, despite PostgreSQL's general advice that CHECK expressions be
immutable.

**Rationale**: FR-007 asks the database to reject *new or updated* violating matches; FR-008 asks
the migration to succeed on a database that already holds violating matches, without manual data
edits, and to be a no-op when re-applied (Alembic `upgrade head` at head does nothing, 001 R10).
`NOT VALID` is exactly "enforce from now on, do not scan the past". Leaving existing violators to the
next ingestion run is what the spec's last edge case prescribes; convergence (R10) removes them for
the same source file. One named constraint per rule makes the rule ↔ constraint mapping a testable
set equality.

**Alternatives rejected**:
- Validated constraints (fail on any existing violator — breaks FR-008).
- A migration that deletes violating matches (data change inside a schema migration; the deleted
  rows would not be recorded as excluded — Principle I — and the migration has no run to attach
  them to).
- A migration that moves violators into `excluded_rows` (needs `raw_record` and `run_id` that a
  migration cannot produce faithfully; duplicates the pipeline's job).
- Conditional `VALIDATE` in the migration when no violators exist (catalog then depends on data
  history; `test_downgrade_then_upgrade_roundtrip` compares `pg_get_constraintdef` text).
- Triggers (same semantics, more code, harder to read in `\d matches`).
- A fixed upper bound such as `match_date <= '2100-01-01'` (not the same rule; future-dated matches
  would pass the database — breaks parity in the direction FR-007 cares about).
- `CURRENT_DATE` (session-time-zone dependent: a session west of UTC near midnight would reject
  rows the UTC-based pipeline accepted, failing the run).

## R9. Why the upsert never trips a `NOT VALID` constraint

**Decision**: The load keeps 001's upsert of matches (001 R11 step 5) unchanged and runs it
**before** the convergence deletes (R10). Argument, recorded so reviewers can check it:

1. Every staged match passed the contract (pipeline) — by parity it satisfies every constraint.
2. The INSERT branch writes a staged tuple → passes.
3. The `ON CONFLICT DO UPDATE` branch sets **every non-key column** from the staged row
   (`_NON_KEY_COLUMNS`, 001 load.py), and the key columns (`league_code`, `match_date`, team ids)
   are equal to the staged row's by definition of the conflict. So the new tuple is exactly the
   staged tuple → passes, whatever the old tuple contained. The only rule reading a key column is
   `date_in_range` (`match_date`), whose value comes from the staged row.
4. `WHERE (old) IS DISTINCT FROM (new)` only skips updates; a skipped update writes nothing.
5. Deletes never trigger CHECK constraints. Teams/leagues upserts touch other tables.

Therefore an existing violating row is either rewritten to a valid tuple (its key reappears in a
valid staged row — e.g. the valid second copy of a match whose first copy now violates), deleted by
convergence (its source row is no longer accepted), or left untouched (it belongs to another source
file — out of scope, still violating, still allowed to exist under `NOT VALID`). None of these
fails. A guard against regressions: `test_upsert_rewrites_existing_violating_match` (integration)
seeds a violating match, re-runs with a valid copy of the same key, and asserts success and the
rewritten values.

Point 3 is a contract on `load.py`: the upsert's SET list must remain "all non-key columns". If a
future change narrowed it, the probe fact "partial UPDATE of a violating row is rejected" applies
and that test fails.

**Rationale**: Upsert-then-delete keeps `match_id` stable for a match whose providing source row
changes (row r violating → its valid duplicate r' takes over the same key: one UPDATE instead of
DELETE + INSERT). Delete-then-upsert would also be correct (R12 ignores surrogate ids) but would
churn ids for no reason.

## R10. Convergence for the same source file (FR-009)

**Decision**: Scope = rows whose provenance is the current file: `matches.source_sha256 = S` and
`excluded_rows.source_sha256 = S`, where S is the SHA-256 of this run's raw file (provenance is
re-stamped on every upsert, 001 R12). Inside the single load transaction, after the match upsert:

- **Stale matches**: delete every match with `source_sha256 = S` that does not correspond to a
  staged row with the same natural key **and** the same `source_row_number`:
  ```sql
  DELETE FROM matches m
  WHERE m.source_sha256 = %(sha)s
    AND NOT EXISTS (
      SELECT 1 FROM stg_matches s
      JOIN teams h ON h.team_key = s.home_team_key
      JOIN teams a ON a.team_key = s.away_team_key
      WHERE s.source_row_number = m.source_row_number
        AND s.league_code = m.league_code AND s.match_date = m.match_date
        AND h.team_id = m.home_team_id AND a.team_id = m.away_team_id)
  ```
  The row count is `matches_removed`.
- **Stale exclusions**: `DELETE FROM excluded_rows WHERE source_sha256 = S AND source_row_number
  NOT IN (staged excluded row numbers)` (anti-join against `stg_excluded`). The row count is
  `excluded_rows_removed`. The existing excluded-row upsert (001 R11 step 7) then inserts new
  exclusions and rewrites changed ones (e.g. `duplicate` → `contract_violation`), keeping the
  first `run_id`.
- Orphan teams and leagues are then removed (R11).

The link between a match and its source row is the provenance pair
(`source_sha256`, `source_row_number`), cross-checked with the natural key. The natural-key check
makes the delete correct even if a future normalization change gave the same row number a
different key (the old row would otherwise survive with a provenance pointing at a row that now
produces another match).

Rows of other files are never touched (spec Assumption). Consequently, after converging S, every
match with provenance S is valid; matches with other provenance may still violate (they predate
the contract), which is why the constraints stay `NOT VALID` (R8).

**Rationale**: This is the smallest rule that makes "derived from this file" equal what a fresh
run would derive (R12): after the run, matches with provenance S = staged rows, excluded rows with
provenance S = this run's exclusions. Everything happens in the existing single transaction under
the advisory lock, so FR-010 (all-or-nothing) holds by construction.

**Alternatives rejected**: delete-by-row-number only (correct for an unchanged file, wrong after a
normalization change, see above); delete by natural key only (cannot tell a stale row from a row of
another file with the same key, since the key is shared across files); truncate-and-reload of
everything (destroys other files' data and excluded-row history, 001 R11); a separate "converge"
command (two commands to keep a database correct; the spec wants the re-run itself to converge).

## R11. Orphan teams and leagues are deleted

**Facts**: In a fresh build every team and league is referenced by at least one match (they are
built from accepted rows only, 001 R7/R26). Real-data profile: removing the 1,381 contract
violators loses no team (1,221 → 1,221) and no league (38 → 38), and no canonical display name
changes.

**Decision**: After the convergence deletes, in the same transaction:
`DELETE FROM teams t WHERE NOT EXISTS (SELECT 1 FROM matches WHERE home_team_id = t.team_id) AND
NOT EXISTS (SELECT 1 FROM matches WHERE away_team_id = t.team_id)` and
`DELETE FROM leagues l WHERE NOT EXISTS (SELECT 1 FROM matches WHERE league_code =
l.league_code)`. Counts are logged (`teams_removed`, `leagues_removed`), not stored. The two orphan
deletes can use existing indexes (`matches_home_team_id_idx`, `matches_away_team_id_idx`, and the
natural-key index led by `league_code`); the stale-match delete of R10 cannot (no index on
`source_sha256`) and is a sequential scan of `matches`, see R15.

**Rationale**: The spec lets the plan choose, provided re-run and fresh build agree. Keeping orphans
would make an upgraded database differ from a fresh build (extra team/league rows) the moment a
team appears only in now-violating rows, so deletion is the only choice that satisfies SC-004 in
general. The rule is global ("no match references it"), not file-scoped, but it can only remove rows
nothing points to, so other files' data is unaffected. Team names need no extra step: the teams
upsert already sets each staged team's name from this run's accepted rows, as a fresh build does.

**Alternatives rejected**: keep orphans (SC-004 fails for the fixture's ghost team, see plan
fixtures); file-scoped team provenance (teams are shared by files; would need a new column and
still not match a fresh build).

## R12. What "identical to a fresh build" means (SC-004, FR-009)

**Decision**: Two databases are identical for this feature when these **content snapshots** are
equal (helper `tests/integration/helpers.py::content_snapshot`):

| Table | Compared content | Excluded (history-dependent) |
|---|---|---|
| `leagues` | `league_code`, ordered | — |
| `teams` | `(team_key, name)`, ordered by `team_key` | `team_id` |
| `matches` | every column as `to_jsonb(m)` minus `match_id`, `home_team_id`, `away_team_id`, plus `home_team_key`, `away_team_key`; ordered by natural key | surrogate ids |
| `excluded_rows` | `to_jsonb(e)` minus `excluded_row_id`, `run_id`; ordered by (`source_sha256`, `source_row_number`) | ids, run link |
| `ingest_runs` | not compared (append-only audit log, spec: "excluding run audit rows") | — |

**Rationale**: Identity values are never reused by PostgreSQL, so an upgraded database (rows
inserted in an earlier run, some deleted) cannot have the same `match_id`s as a fresh one; and
`excluded_rows.run_id` is defined as "first run that recorded the row" (001 R12), which is history
by definition. Everything that describes the data — every source value, provenance, team names,
exclusion reason/detail/raw record — is compared. `to_jsonb` covers all columns automatically, so a
column added later cannot be forgotten by the test. 001's stronger guarantee (same file twice →
identical **including** ids) still holds and keeps its own test.

**Alternatives rejected**: comparing row counts only (explicitly insufficient per the user's
instruction and SC-004 "0 differences"); comparing with ids (impossible by construction, see above);
`pg_dump` diffs (needs the client binary in the test environment and sorts unpredictably).

## R13. Testing the upgrade path and parity

**Decision**:
- **Pre-contract state**: `helpers.seed_pre_contract(db_url, source, run_date) -> str` migrates an
  empty database to `0001_initial_schema` and writes what feature 001 would have loaded: it runs
  the pure stages with `contract=PRE_CONTRACT` (so contract violators are accepted and their valid
  duplicates excluded as `duplicate`), builds the batch, and inserts leagues, teams, matches, one
  0001-shaped `ingest_runs` row and the excluded rows with plain SQL in one transaction. It returns
  the file's SHA-256 (same bytes → same S as the pipeline). The test then runs
  `helpers.upgrade(url)` (FR-008) and `pipeline.run` (convergence). A second, fresh database cloned
  from the head template is loaded once; `content_snapshot`s must be equal.
- **Shared parity cases**: `tests/contract_cases.py` holds one table of cases (`case_id`, source
  value overrides on a complete valid row, expected violated rule ids, with `{run_date}` /
  `{run_date+1}` placeholders). The unit test runs each case through `validate.partition`. The
  integration test compares **every constraint separately** (plan review P1-3), because PostgreSQL
  reports only the first failing CHECK. It copies the parsed row into a temp table
  `CREATE TEMP TABLE probe (LIKE matches INCLUDING DEFAULTS INCLUDING IDENTITY)`, which copies no CHECK constraints. For each
  `matches_contract_*` constraint it reads the expression with `pg_get_expr(conbin, conrelid)` and
  evaluates `SELECT (<expr>) IS NOT FALSE FROM probe`. It then asserts that the set of failing
  constraint names equals `{matches_contract_<r> for r in case.violated}`. It also asserts that a
  real INSERT into `matches` is accepted iff that set is empty. The integration test uses the
  database's UTC date as the run date. The same per-constraint comparison also runs over every row
  of `matches_contract.csv`, `matches_edge_cases.csv` and `matches_real_sample.csv` (parsed with
  `PRE_CONTRACT`), against `find_violations` with `CURRENT_CONTRACT`, as a cheap differential test.
- **Name parity**: the set of contract rule ids equals the set of `matches_contract_*` constraint
  names in the catalog.

**Rationale**: The seeding helper is the only way to obtain a 0001-era database with violating
matches: at head the new constraints would reject them. Producing that state from the same pure
stages (with the empty contract) rather than hand-written rows makes it exactly what 001 loaded for
that file. One shared case table means a rule cannot be added to the pipeline without its database
twin being tested, and vice versa.

**Alternatives rejected**: checking out feature 001's code in tests (needs git history at test
time); dropping the constraints, loading, re-adding them (tests a state the migration never
produces); a test-only flag in `load_run` to bypass the contract (production code carrying test
modes).

## R14. New error: `ContractSchemaError`, exit 9

**Decision**: `errors.ContractSchemaError(FootballStatsError)` with `exit_code = 9`,
`error = "contract_schema_error"`, raised by `data_contract.find_violations` for any non-rule failure
case (R2). Added to the CLI contract and the run-report schema's error enum.

**Rationale**: It is a named internal invariant (the parsed frame does not have the dtypes the
contract declares), like `AccountingError` (001 decision P1-8): failing loudly before the database
is touched is the constitution's "explicit error handling"; a dedicated code tells an operator it is
a bug, not bad data.

**Alternatives rejected**: exit 1 `unexpected_error` (loses the distinction); converting such
failures into exclusions (one parser regression would exclude every row and "succeed").

**Also**: `errors.ClockSkewError(FootballStatsError)`, `exit_code = 10`, `error = "clock_skew"`, raised by
`load_run` before any write when the database's UTC date is earlier than the run date (R8). It is a
separate code because the remedy (fix a clock) differs from a database failure (exit 7).

## R15. Performance (SC-005)

**Facts (measured, Apple M3, 2026-10-15)**: read + parse + partition of the real file (no
contract): 1.5 s. The nine rule expressions over 238,854 rows: 0.002 s as raw polars. pandera
`validate(lazy=True)` worst case (240k rows, every row failing two checks): 0.53 s. 001's full run
(download + clean + load): 18.7 s (README).

**Decision**: No sampling, no chunking: one vectorized validation over the candidate frame, with
only `source_row_number` and the 17 rule input columns selected before calling pandera (keeps the
JSON `failure_case` payloads small). Convergence deletes are set-based anti-joins: the stale-match
delete scans all of `matches` (about 238k rows, no index on `source_sha256`), and the stale-exclusion
delete scans `excluded_rows` for this file. Both are acceptable at this scale, and no index is added for
them (YAGNI; the measured timing in T036 decides). The temp tables are filled by COPY and have no
statistics, so `load_run` runs `ANALYZE stg_matches, stg_excluded` after the COPY. Without that the
planner would estimate the anti-joins from default row counts and could choose a nested loop
(plan review P1-9). SC-005 is
measured manually (task T036, quickstart §3), never asserted in tests (001 R25: wall-clock
assertions are flaky).

**Rationale**: The added work is well under a second against a 5-minute budget; anything more
elaborate is unjustified (Principle V).

## R16. Real-data profile and the FR-011 review threshold (planning estimate)

Measured with the method described at the top of this document on the pinned file, accepted rows after feature 001's checks
(238,854), rules as in R3, run date 2026-10-15:

| Rule | Violating rows | Share of rows read |
|---|---:|---:|
| `ht_le_ft_home`, `ht_le_ft_away` | 0, 0 | 0 % |
| `ft_result_consistent`, `ht_result_consistent` | 0, 0 | 0 % (matches 001 R2) |
| `sot_le_shots_home` | 7 | 0.003 % |
| `sot_le_shots_away` | 13 | 0.005 % |
| `odds_min` | 8 (7 × `odd_home = 0.0`, 1 × `max_odd_away = 0.0`) | 0.003 % |
| `max_odds_ge_avg` | 1,357 (home 469, draw 409, away 534 terms) | 0.568 % |
| `date_in_range` | 0 (dates 2000-07-28 … 2026-09-03) | 0 % |
| rows with ≥ 1 violation | 1,381 (4 rows violate two rules) | 0.578 % |

Expected fresh run: rows read 238,859; loaded 237,473; excluded 1,386 (`missing_field` 1,
`missing_result` 4, `contract_violation` 1,381); leagues 38; teams 1,221. Expected re-run of the
upgraded feature-001 database: `matches_removed` 1,381, `matches_unchanged` 237,473, 0 inserted/updated.

No rule exceeds the 1 % review threshold of FR-011. `max_odds_ge_avg` is the largest (0.57 %):
violations cluster in 2005–2012 and lower divisions (F2, E3, E2, E1, SC2…), typically
`max` a few hundredths below `avg` (e.g. 2.39 vs 2.40), consistent with averages and maxima taken
from different bookmaker sets or times in the original source. The decision for this rule (keep, as
the spec defines it) and the actual measured numbers are recorded by task T036 in `decisions.md`
and the README. If the real run differs from this table, T036 stops and reports instead of editing
expectations (same discipline as 001 T053).

## R17. Existing fixture that violates the new contract

**Fact**: Running the proposed rules on every existing fixture: `matches_valid.csv` (12) and
`matches_real_sample.csv` (14 accepted) pass; `matches_edge_cases.csv` rows 19 and 21 (Twente v
Ajax and its case/whitespace duplicate) have `FTHome,FTAway,FTResult = 0,2,A` but
`HTHome,HTAway,HTResult = 1,0,H` — a half-time lead of 1 with 0 full-time goals.

**Decision**: Repair those two rows to `HTHome,HTAway,HTResult = 0.0,1.0,A` (task T004). Their
purpose in 001 is team-name normalization and duplicate detection, which is unchanged;
`matches_edge_cases.expected.json` stays as it is. Contract cases get their own fixture
(`matches_contract.csv`).

**Rationale**: The rows were never meant to test half-time logic; leaving them would silently change
two 001 tests' meaning (row 19 would become a contract violation and row 21 would no longer be a
duplicate).

**Alternatives rejected**: updating the 001 expectations instead (would delete the duplicate-pair
coverage that `test_duplicate_detail_points_to_kept_row` relies on).

## R18. Two revisions, and downgrade

**Decision**: The schema change is split into two Alembic revisions:
`0002_contract_run_metadata` (the four `ingest_runs` columns of R7 and the seven-code
`excluded_rows_reason_check`) and `0003_contract_constraints` (the nine `NOT VALID` constraints of
R8). `0003.downgrade()` drops the nine constraints. `0002.downgrade()` drops the four columns and
restores the six-code reason CHECK **validated**; if `contract_violation` rows exist the restore
fails with a check violation and that revision's transaction is rolled back. Note that
`migrations/env.py` uses `transaction_per_migration=True`: a `downgrade 0001_initial_schema` from
head first **commits** `0003`'s downgrade (constraints dropped) and then refuses `0002`'s, so the
database stops at `0002_contract_run_metadata`, whose schema matches its `alembic_version` row. No
exclusion record is lost; the operator must decide what to do with those rows before downgrading
further. Tested by `test_downgrade_refused_with_contract_violation_rows` (plan review P1-7).

**Rationale**: User Story 1 (pipeline contract, P1) needs only `0002`; User Story 2 (database
enforcement, P2) adds `0003`. Separate revisions let each story be delivered and tested on its own
and keep "tests before code" true for the constraints (their tests are written in the US2 phase,
before `0003` exists). A downgrade must not silently delete exclusion records (Principle I). The
automated round-trip test runs on an empty database.

**Alternatives rejected**: deleting `contract_violation` rows on downgrade (silent data loss);
restoring the old CHECK `NOT VALID` (leaves a constraint the old code never expected).

## R19. Dependency on documentation (FR-012, SC-006)

**Decision**: The README gains a "Data contract" section (version, the nine rules with descriptions,
link to `contracts/data-contract.md`) and a per-rule table measured by T036. A unit test
(`tests/unit/test_readme_contract.py::test_readme_documents_every_rule`) asserts that the README
names `CONTRACT_VERSION` and every rule id of `CURRENT_CONTRACT`, so adding a rule without
documenting it fails the gate. The numbers themselves are verified manually (T036) because they
come from the network-dependent real run.

**Rationale**: Automates the part of FR-012 that can drift silently (rule list) without pretending a
test can check real-data numbers offline.
