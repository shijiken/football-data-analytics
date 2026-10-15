# Feature Specification: Match Data Contract

**Feature Branch**: `002-data-contract`

**Created**: 2026-10-15

**Status**: Draft

**Input**: User description: "Phase 2 — Formal data contract validation. Validate cleaned match rows against an explicit, declarative data contract (pandera DataFrame schema) before they reach the database, adding cross-field business rules on top of the existing per-column parsers: half-time goals <= full-time goals (each side); FTResult/HTResult consistent with the goals; shots on target <= shots (each side); decimal odds >= 1.0 and max odds >= average odds; match date between 2000-01-01 and the run date (no future matches). Rows violating any rule are excluded (never silently dropped) with reason "contract_violation" and a detail naming every failed rule; the run summary reports counts per rule. Matching database CHECK constraints are added through a migration so the DB enforces the same contract (constraint parity). Re-running after the contract changes must converge the database to exactly what a fresh run would produce: matches loaded earlier that now violate the contract are removed from matches and recorded as excluded. Contract is versioned and the version is recorded per ingest run. Must be measured against the real dataset (how many rows per rule) and documented in README."

## Context

Feature 001 (Match Data Ingestion) loads the dataset with per-column checks only: each value
must parse (dates, counts, decimals, result codes), required fields must be present, and
duplicates and same-team matches are excluded. It does not check whether the values in a row
are **consistent with each other** (e.g. a recorded home win with a 0–2 score). Feature 001
explicitly deferred these "schema rules per column" to Phase 2, and the constitution
(Principle I) requires data to be validated against an explicit data contract before it
reaches the database's clean layers.

## User Scenarios & Testing *(mandatory)*

The primary user is the project developer (and anyone reviewing or running the project) who
analyses the loaded matches and needs to trust that every stored match is internally
consistent.

### User Story 1 - Only internally consistent matches are loaded (Priority: P1)

The developer runs the existing ingestion command. Every row is now also checked against a
written set of business rules (the data contract). Rows that break any rule are not loaded;
they are recorded as excluded with the names of every rule they broke, and the run summary
shows how many rows broke each rule.

**Why this priority**: This is the feature's core value: analyses (home advantage, goals per
season, form) silently go wrong if contradictory rows are loaded.

**Independent Test**: Run ingestion on a small sample file containing one row per rule
violation plus valid rows; confirm only the valid rows are loaded, each violating row is
excluded naming the exact rule(s), and read = loaded + excluded.

**Acceptance Scenarios**:

1. **Given** a row whose half-time home goals exceed its full-time home goals, **When**
   ingestion runs, **Then** the row is not loaded and is recorded as excluded with reason
   "contract violation" and a detail naming the half-time/full-time rule.
2. **Given** a row whose recorded full-time result does not match its score (e.g. result "H"
   with score 0–2), **When** ingestion runs, **Then** the row is excluded naming the result
   consistency rule.
3. **Given** a row that breaks two rules at once, **When** ingestion runs, **Then** the single
   exclusion record names both rules, and the per-rule counts in the run summary count it once
   under each rule.
4. **Given** a row with missing optional statistics (e.g. no half-time score, no shots, no
   odds), **When** ingestion runs, **Then** the rules involving those statistics are skipped
   for that row and it is loaded if every other rule passes.
5. **Given** a completed run, **When** the developer views the run summary, **Then** it shows
   rows read, loaded, excluded per reason, and violations per contract rule (zero counts
   included).

---

### User Story 2 - The database itself enforces the contract (Priority: P2)

Even if someone bypasses the pipeline (manual insert, a future bug), the database refuses any
match that breaks the contract, so the stored data is trustworthy regardless of how it got
there.

**Why this priority**: Defence in depth. The pipeline filter (Story 1) delivers the value; this
guarantees it stays true.

**Independent Test**: Against a migrated database, attempt to insert one match violating each
rule directly; every insert is rejected. Insert a valid match; it is accepted.

**Acceptance Scenarios**:

1. **Given** a migrated database, **When** a match violating any contract rule is inserted
   directly, **Then** the database rejects it.
2. **Given** an existing database already containing matches loaded under the previous
   version, **When** the structure change is applied, **Then** it succeeds without the
   developer manually editing data first, and afterwards the database enforces every rule.
3. **Given** a database at the latest structure version, **When** structure changes are applied
   again, **Then** nothing changes and no error occurs.

---

### User Story 3 - Re-runs converge to a fresh-run state (Priority: P2)

When the contract changes (rules added or tightened), the developer re-runs ingestion on the
same source file and the database ends up exactly as if it had been built from scratch with
the new contract: no stale matches that now violate rules, and no stale exclusion records for
rows that now pass.

**Why this priority**: Required by the constitution's idempotency principle (any layer can be
rebuilt), and it is the realistic upgrade path for the existing database.

**Independent Test**: Load a sample file with the previous rules (some contract-violating rows
get loaded), then run ingestion with the contract on the same file; compare every table
(excluding run audit rows) with a fresh database loaded once with the contract. They match.

**Acceptance Scenarios**:

1. **Given** matches loaded earlier that violate the current contract, **When** ingestion runs
   again on the same source file, **Then** those matches are removed from the loaded matches
   and recorded as excluded with the violated rules.
2. **Given** a row excluded in an earlier run that passes under the current rules, **When**
   ingestion runs again on the same source file, **Then** it is loaded and its old exclusion
   record is removed.
3. **Given** a database already converged, **When** ingestion runs again with the same file and
   contract, **Then** match, team, league, and excluded-row contents are unchanged.

---

### User Story 4 - Know how the real dataset measures up (Priority: P3)

The developer can see, for the real dataset, how many rows each rule rejects and which contract
version produced a given run, so they can judge whether each rule is correct and explain the
data quality in the README.

**Why this priority**: Supports trust and the project's documentation goals; not needed to
deliver Stories 1–3.

**Independent Test**: Look up any past run and confirm it shows the contract version in force;
open the README and find a per-rule table of violation counts for the real dataset.

**Acceptance Scenarios**:

1. **Given** any completed run, **When** the developer looks up the run, **Then** it shows the
   contract version used and violations per rule.
2. **Given** the real dataset has been ingested, **When** the developer reads the README,
   **Then** it shows the number (and share) of rows rejected by each rule, measured from an
   actual run, with the run date and contract version.

---

### Edge Cases

- A row breaks a rule **and** is a duplicate of another row: contract checks happen before
  duplicate detection, so a valid copy of a match is never lost because an earlier copy of it
  was invalid; the invalid copy is excluded as a contract violation.
- A row breaks per-column rules from feature 001 (e.g. unparseable value) and contract rules:
  it is excluded with the feature 001 reason; contract rules are only evaluated on rows that
  passed per-column checks.
- Only one side of a rule's inputs is present (e.g. home shots present, home shots on target
  missing): the rule is skipped for that row.
- A rule that would reject an unexpectedly large share of the real data (more than 1% of rows)
  may indicate a wrong rule rather than bad data: it must be reviewed and the decision (keep,
  adjust, or drop) recorded with its reasoning before the feature is finished.
- Matches dated in the future relative to the run date are excluded; a match dated exactly on
  the run date is allowed.
- The structure change is applied to a database containing matches that violate the new rules:
  the change must not fail; violating matches are left for the next ingestion run to converge
  (Story 3), while the database still rejects any **new** violating insert or update.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST define the data contract in a single, declarative, versioned
  place in the codebase; each rule MUST have a stable short identifier and a human-readable
  description.
- **FR-002**: The contract MUST include at least these rules, each applied only when all of its
  inputs are present:
  - **ht_le_ft_home / ht_le_ft_away**: half-time goals ≤ full-time goals (each side).
  - **ft_result_consistent**: full-time result is "H" if home goals > away goals, "D" if equal,
    "A" otherwise.
  - **ht_result_consistent**: half-time result consistent with half-time goals in the same way.
  - **sot_le_shots_home / sot_le_shots_away**: shots on target ≤ shots (each side).
  - **odds_min**: every pre-match decimal odd (average and maximum, home/draw/away) ≥ 1.0.
  - **max_odds_ge_avg**: each maximum odd ≥ the corresponding average odd.
  - **date_in_range**: match date between 2000-01-01 and the run date, inclusive.
- **FR-003**: Contract validation MUST run on every row that passed feature 001's per-column
  checks, before duplicate detection and before anything is written to the database.
- **FR-004**: A row violating one or more rules MUST NOT be loaded; it MUST be recorded as an
  excluded row with reason "contract_violation" and a detail listing every violated rule
  identifier.
- **FR-005**: The run summary (console output and the stored run record) MUST include
  violations per rule identifier, with zero counts for rules nothing violated. Rows read MUST
  still equal rows loaded + rows excluded.
- **FR-006**: Every ingestion run record MUST store the contract version in force.
- **FR-007**: The database MUST reject any newly inserted or updated match that violates a
  contract rule, via versioned structure changes. Every rule the database enforces MUST have a
  matching pipeline rule that excludes such rows first (constraint parity), so bad data
  becomes an excluded row, never a failed run.
- **FR-008**: Applying the structure change to an existing database MUST succeed even if it
  contains matches that violate the new rules, and applying it again MUST be a no-op.
- **FR-009**: After a successful run on a source file, the loaded matches and excluded-row
  records derived from that file MUST equal what a fresh run on an empty database would
  produce with the current contract (convergence), including removing previously loaded
  matches that now violate the contract and removing exclusion records for rows that now pass.
- **FR-010**: A run MUST remain all-or-nothing (feature 001 FR-012): if any step fails, matches,
  teams, leagues, and exclusion records are left exactly as before the run.
- **FR-011**: Each rule MUST be measured against the real dataset; any rule rejecting more than
  1% of rows MUST be reviewed and the outcome recorded with reasoning.
- **FR-012**: The README MUST document the contract (rules and version) and a per-rule
  violation table measured from a real run on the full dataset.

### Key Entities *(include if feature involves data)*

- **Data Contract**: The versioned set of business rules a match row must satisfy. Attributes:
  version, and per rule an identifier, a description, and the inputs it reads.
- **Contract Violation**: One rule broken by one source row. Recorded as part of the excluded
  row's detail and counted in the run's per-rule summary.
- **Ingest Run** (extended from feature 001): additionally records the contract version and
  violations per rule.
- **Excluded Row** (extended from feature 001): gains the "contract_violation" reason.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 0 loaded matches violate any contract rule after a run (verified by querying the
  stored data against every rule).
- **SC-002**: 100% of source rows remain accounted for after every run: loaded + excluded =
  read.
- **SC-003**: For every rule, a direct insert of a violating match into the database is rejected
  (100% of rules enforced at the storage level).
- **SC-004**: Upgrading an existing database and re-running ingestion produces table contents
  identical to a fresh build with the contract (0 differences in matches, teams, leagues, and
  excluded rows).
- **SC-005**: A full ingestion of the real dataset still completes in under 5 minutes on a
  typical laptop (feature 001 SC-002 is not regressed), with the time measured and recorded.
- **SC-006**: The README reports per-rule violation counts for the real dataset taken from an
  actual run, not estimates.

## Assumptions

- **User-mandated technology**: the contract is expressed as a declarative DataFrame schema
  (pandera), as named in the feature request and PLAN.md; database enforcement uses CHECK
  constraints added by a migration. These are constraints to honour in the plan.
- Per-column parsing, required fields, same-team, and duplicate rules from feature 001 stay as
  they are; this feature adds the cross-field contract on top of them and does not re-implement
  them.
- Convergence (FR-009) is scoped to re-runs on the **same source file**. Removing matches that
  came only from an older, different source file is out of scope.
- Teams and leagues that end up referenced by no match after convergence may be removed or kept;
  the plan decides, provided a re-run and a fresh build produce the same result.
- "Run date" is the date the run starts, in UTC.
- Elo/form columns and betting-market columns other than the average and maximum home/draw/away
  odds are not covered by the initial contract; they may be added in a later contract version.
- Statistical anomaly detection (outlier scores, e.g. 15–0) is out of scope; the contract checks
  logical consistency, not plausibility.
