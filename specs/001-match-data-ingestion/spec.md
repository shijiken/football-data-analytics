# Feature Specification: Match Data Ingestion

**Feature Branch**: `001-match-data-ingestion`

**Created**: 2026-10-11

**Status**: Draft

## Clarifications

### Session 2026-10-11

- Q: Should Phase 1 guarantee no duplicates on re-runs and persist excluded rows, or defer
  both to Phase 2? → A: Both now: re-runs are idempotent and excluded rows are stored in the
  database (FR-011, FR-013).

**Input**: User description: "Ingest the Club Football Match Data CSV (Gábor, 2025; https://github.com/xgabora/Club-Football-Match-Data-2000-2025/): download the raw match data, clean it (handle nulls, normalize team names, parse dates, remove duplicates), and load it into a PostgreSQL database running in Docker Compose, with a relational schema for teams, leagues, and matches managed as migrations."

## User Scenarios & Testing *(mandatory)*

The primary user is the project developer (and later, anyone reviewing or running the
project) who needs a trustworthy, queryable copy of 25 years of club football match results.

### User Story 1 - Load match data into a queryable database (Priority: P1)

The developer runs a single ingestion command. The system fetches the published match data,
cleans it, and stores it in the project database organized into leagues, teams, and matches,
so the developer can immediately query results (e.g. "all Premier League matches in 2015").

**Why this priority**: Every later phase (validation, transforms, API, dashboard) depends on
having clean match data in the database. Without this, nothing else can be built.

**Independent Test**: Start the project's database from an empty state, run the ingestion
command, and query the database: matches, teams, and leagues are present, and each match
links to its league and both teams.

**Acceptance Scenarios**:

1. **Given** an empty database and network access to the data source, **When** the developer
   runs the ingestion command, **Then** the match data is downloaded, cleaned, and stored, and
   the command reports how many rows were read, loaded, and excluded.
2. **Given** loaded data, **When** the developer queries matches for a specific league and
   season, **Then** each result shows the match date, home team, away team, and final score.
3. **Given** loaded data, **When** the developer looks up a team, **Then** that team appears
   exactly once regardless of spelling variations (extra spaces, inconsistent casing) in the
   source.

---

### User Story 2 - Set up the database structure from scratch (Priority: P2)

The developer (or a new contributor) starts the project on a fresh machine and creates the
complete database structure with one command, without writing or running any manual setup
steps.

**Why this priority**: Reproducible setup is required by the project's one-command principle,
but it only has value once there is data to load into it (Story 1).

**Independent Test**: On a machine with only the project's prerequisites installed, start the
stack and apply the database structure; confirm all expected tables, keys, and relationships
exist and that applying the structure again makes no changes.

**Acceptance Scenarios**:

1. **Given** a fresh environment, **When** the developer starts the project stack, **Then** the
   database becomes available without manual configuration.
2. **Given** a running empty database, **When** the developer applies the versioned structure
   changes, **Then** leagues, teams, and matches tables exist with their relationships enforced.
3. **Given** a database already at the latest structure version, **When** the structure changes
   are applied again, **Then** nothing changes and no error occurs.

---

### User Story 3 - Understand what was excluded and why (Priority: P3)

After an ingestion run, the developer can see which source rows were not loaded (duplicates,
unparseable dates, missing required fields) and the reason for each, so they can trust that no
data disappeared silently.

**Why this priority**: Required by the constitution's "No Silent Data Loss" principle, but the
core value (Story 1) can be demonstrated first.

**Independent Test**: Run ingestion on a small sample file containing known bad rows
(a duplicate, a malformed date, a missing team) and confirm each one is reported with the
correct reason and that read = loaded + excluded.

**Acceptance Scenarios**:

1. **Given** source data containing a duplicated match, **When** ingestion runs, **Then** only
   one copy is loaded and the duplicate is recorded as excluded with reason "duplicate".
2. **Given** a row with an unparseable date or missing team, **When** ingestion runs, **Then**
   the row is not loaded and is recorded with the specific reason.
3. **Given** a completed run, **When** the developer queries the excluded rows in the database,
   **Then** each one shows its original content and reason.
4. **Given** data already loaded, **When** ingestion runs again on the same source, **Then**
   match, team, league, and excluded-row counts are unchanged.

---

### Edge Cases

- The data source is unreachable or the download is interrupted: the run fails with a clear
  error and the database is left unchanged.
- The source file's columns differ from what is expected (renamed or missing column): the run
  stops before loading and names the missing columns.
- The ingestion command is run twice on the same data: the second run changes nothing and
  reports the same counts (FR-011).
- A match has missing optional statistics (shots, corners, odds) but valid core fields: the
  match is loaded with those values left empty, not excluded.
- A match has a missing final score (e.g. postponed/abandoned): it is excluded with reason
  "missing result".
- The same team name appears with different whitespace or casing: treated as one team.
- The same team name appears in different leagues across seasons (promotion/relegation): it is
  still one team.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST download the match data file from the published dataset source and
  keep an unmodified copy of the raw file for the run.
- **FR-002**: System MUST check that the source file contains the expected columns before
  processing, and stop with a clear error naming any missing columns.
- **FR-003**: System MUST parse match dates into a proper date value; rows whose date cannot be
  parsed MUST be excluded with reason "invalid date".
- **FR-004**: System MUST normalize team names (trim surrounding whitespace, collapse repeated
  spaces, consistent casing) so the same team is stored once.
- **FR-005**: System MUST treat missing optional statistics as empty values, and MUST exclude
  rows missing a required field (date, league, home team, away team, final score) with a
  reason naming the field.
- **FR-006**: System MUST identify duplicate matches (same league, date, home team, and away
  team), load one copy, and record the others as excluded with reason "duplicate".
- **FR-007**: System MUST store data as three related entities: leagues, teams, and matches,
  where every match references one league, one home team, and one away team.
- **FR-008**: System MUST enforce that a match's home team and away team are different, and
  that a match cannot reference a league or team that does not exist.
- **FR-009**: The database structure MUST be created and changed only through versioned,
  ordered structure changes that can be applied from scratch with one command.
- **FR-010**: At the end of each run, the system MUST report rows read, rows loaded, and rows
  excluded (with a count per reason), and read MUST equal loaded + excluded.
- **FR-011**: Running ingestion again on the same source data MUST leave the database in the
  same state as a single run: no duplicate leagues, teams, matches, or excluded-row records.
- **FR-013**: Every excluded row MUST be persisted in the database together with its original
  content, the exclusion reason, and the run it came from, so exclusions can be queried after
  the run.
- **FR-012**: If any step fails, the system MUST leave the database in its previous state
  (no partially loaded data).

### Key Entities *(include if feature involves data)*

- **League**: A competition division as identified in the source data (e.g. a code like "E0"
  for the English top flight). Attributes: code, optional country and display name.
- **Team**: A club, identified by its normalized name. A team can appear in many leagues over
  time.
- **Match**: One played fixture. Attributes: date (and kick-off time if available), league,
  home team, away team, full-time score and result, optional half-time score, optional match
  statistics (shots, shots on target, corners, fouls, cards), optional pre-match ratings/form,
  and optional betting odds.
- **Excluded Row**: A source row that was not loaded, with the original row content and the
  reason for exclusion.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer with only the project prerequisites installed can go from a fresh
  checkout to a fully loaded database in under 10 minutes using no more than two commands.
- **SC-002**: A full ingestion of the complete dataset (200,000+ matches) finishes in under
  5 minutes on a typical laptop.
- **SC-003**: 100% of source rows are accounted for after every run: rows loaded plus rows
  excluded equals rows read.
- **SC-004**: Spot-checking 20 randomly chosen matches against the source shows 0 differences
  in date, teams, and final score.
- **SC-005**: Running ingestion twice in a row produces identical record counts in every
  table (0 duplicates created by re-runs).
- **SC-006**: Each real-world team appears exactly once in the teams list (no near-duplicates
  caused by whitespace or casing).

## Assumptions

- **User-mandated technology** (from the feature request and constitution): the database is
  PostgreSQL running in Docker Compose, and structure changes are managed as migrations. These
  are constraints, detailed in the plan rather than this spec.
- Only the dataset's match results file is in scope; the separate Elo ratings file is out of
  scope for this feature.
- Name normalization covers formatting differences only (whitespace, casing). Mapping genuinely
  different names for the same club (e.g. "Man United" vs "Manchester Utd") is out of scope.
- League codes are kept as provided by the source; enriching them with full names and
  countries is optional and may use a small static lookup.
- Formal data-contract validation (schema rules per column) is Phase 2 work; this feature only
  applies the checks listed in FR-002 to FR-006.
- Transforms, analytics tables, orchestration, the API, and the dashboard are out of scope.
- The developer has internet access when running ingestion; offline runs from an already
  downloaded file are a nice-to-have, not required.
