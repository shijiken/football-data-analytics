# Feature Specification: Pipeline Orchestration

**Feature Branch**: `004-orchestration`

**Created**: 2026-10-18

**Status**: Draft

**Input**: User description: "Phase 4 of PLAN.md — orchestration & containers: containerize the pipeline with a multi-stage Dockerfile and small image; orchestrate with Prefect (or Airflow): retries, logging, scheduled runs; `docker compose up` brings up the entire stack in one command. Also address the follow-up findings from the PR #1 and PR #2 reviews."

## Context

Features 001–003 provide three steps that are run by hand, in order: apply migrations, ingest
the match data, build the analytics transforms. Nothing runs them on a schedule, retries a
transient failure (e.g. a network blip during download), or shows the history of runs in one
place. The constitution requires the full stack to start with `docker compose up`. The PR #1
and PR #2 reviews also left small, non-blocking findings, several of which matter once runs are
scheduled and can overlap.

## User Scenarios & Testing *(mandatory)*

The primary user is the project developer (and a reviewer running the project), who wants the
data kept up to date without manual steps and wants to see what ran and whether it worked.

### User Story 1 - One command brings up a self-updating stack (Priority: P1)

The developer runs `docker compose up`. The database starts, migrations apply, and the
orchestrator starts with the pipeline registered. A full pipeline run (ingest, then transform)
happens automatically on startup and then on a schedule. The developer can open a web UI to
see runs, their status, and their logs.

**Why this priority**: This is the phase's core value and a constitution requirement
(Principle IV).

**Independent Test**: On a clean machine with only the prerequisites, run `docker compose up`
against a small fixture source; within a few minutes a run completes successfully, the marts are
populated, and the run is visible with its logs in the UI.

**Acceptance Scenarios**:

1. **Given** a clean environment, **When** the developer runs `docker compose up`, **Then** all
   services start without manual steps and a first pipeline run starts automatically.
2. **Given** the stack is running, **When** the schedule fires, **Then** a new run executes
   ingest then transform, in that order, and transform never starts if ingest failed.
3. **Given** a completed run, **When** the developer opens the orchestrator UI, **Then** the run,
   each step's status, duration, and logs (including the ingest row counts) are visible.
4. **Given** the stack is stopped and started again, **When** it comes up, **Then** previous run
   history is still visible and the data is unchanged until the next run.

---

### User Story 2 - Transient failures are retried, real failures are not (Priority: P1)

A transient failure (source temporarily unreachable, database briefly unavailable) is retried
automatically a bounded number of times with a delay. A permanent failure (missing columns in
the source, a data-test failure, a contract schema error) fails the run immediately and clearly,
without retrying.

**Why this priority**: Retries are only safe because steps are idempotent (Principle II); this
story is where that pays off, and wrong retries waste time or hide bugs.

**Independent Test**: Simulate an unreachable source that recovers after the first attempt: the
run succeeds on a retry. Simulate a source with missing columns: the run fails after one
attempt, with the error named.

**Acceptance Scenarios**:

1. **Given** the source is unreachable on the first attempt and reachable on the second,
   **When** a run executes, **Then** the ingest step is retried and the run succeeds, with the
   retry visible in the run history.
2. **Given** a failure classified as permanent, **When** a run executes, **Then** the step fails
   on its first attempt and the run is marked failed with the error category.
3. **Given** a step that keeps failing transiently, **When** the retry limit is reached, **Then**
   the run fails and the database is left as it was before the run (feature 001 FR-012).

---

### User Story 3 - Overlapping runs cannot corrupt data (Priority: P2)

If a scheduled run starts while another is still running (or the developer triggers one
manually), the runs cannot interfere: the second either waits or is refused clearly, and no
downloaded or loaded data is corrupted.

**Why this priority**: Scheduling makes overlap realistic. The PR #1 review found that two
containers downloading at once can write to the same temporary file.

**Independent Test**: Start two runs concurrently against the same stack; both end in a defined
state (one succeeds, the other waits or is refused) and the raw file and tables are identical to
a single run.

**Acceptance Scenarios**:

1. **Given** a run in progress, **When** a second run starts, **Then** it does not run its steps
   at the same time as the first (it waits or is refused with a clear message).
2. **Given** two processes download the source at the same moment, **When** both finish,
   **Then** the stored raw file's content matches its content hash.
3. **Given** a cached raw file whose content no longer matches its hash, **When** a run uses it,
   **Then** the run detects the mismatch and fetches again (or fails clearly) instead of trusting
   it.

---

### User Story 4 - Small, lean images (Priority: P3)

The application images are built in stages so the runtime images contain only what they need,
with a measured size recorded in the README.

**Why this priority**: Good container practice and an explicit PLAN.md item, but no user-visible
behaviour depends on it.

**Independent Test**: Build the images and compare their sizes to the previous single-stage
image; confirm the runtime images contain no build tools or test dependencies and run as a
non-root user.

**Acceptance Scenarios**:

1. **Given** the images are built, **When** the developer inspects them, **Then** they contain no
   development or test dependencies, no build tool caches, and run as a non-root user.
2. **Given** the README, **When** the developer reads it, **Then** it lists each image's measured
   size before and after this feature.

---

### User Story 5 - Review follow-ups (Priority: P3)

The non-blocking findings from the PR #1 and PR #2 reviews are resolved or explicitly declined
with a reason.

**Why this priority**: Housekeeping that keeps the codebase honest; independent of Stories 1–4.

**Independent Test**: Each finding has a matching test or a recorded decision.

**Acceptance Scenarios**:

1. **Given** `verify --source-file` with a file other than the latest ingested one, **When** it
   runs, **Then** it fails clearly instead of comparing unrelated rows.
2. **Given** `verify` on a malformed file, **When** it runs, **Then** it returns the same typed
   error and exit code that ingest returns for that input.
3. **Given** the remaining findings (verify efficiency, index on the source hash, repeated count
   computation, failure-case loop, SQL string style, cross-file violators note), **When** the
   feature is done, **Then** each is fixed or its decision is recorded.

---

### Edge Cases

- The orchestrator server is down when the schedule would fire: the missed run is not run
  twice when it comes back (at most one catch-up run).
- The transform fails its data tests after a successful ingest: the run is failed, ingested
  data stays, and the next run retries both steps (both are idempotent).
- The developer runs the pipeline manually from the command line while the stack is up: it is
  subject to the same overlap protection.
- The database container restarts mid-run: the run fails (or retries as transient) and leaves no
  partial load.
- Secrets (database password) are never shown in run logs or the UI.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST define one pipeline flow with ordered steps: migrate (or verify the
  schema is current), ingest, transform. A step MUST NOT start if an earlier step failed.
- **FR-002**: `docker compose up` MUST start the database, the orchestrator (server and UI), and a
  runner that executes the flow, with no other manual steps; a first run MUST start
  automatically.
- **FR-003**: The flow MUST run on a configurable schedule (default once a day), configured via
  environment variables.
- **FR-004**: Each step MUST classify failures as transient (retried up to a configurable limit
  with a delay) or permanent (not retried), based on the existing typed errors and exit codes.
- **FR-005**: Run history, per-step status, duration, retries and logs MUST be visible in the
  orchestrator UI and MUST survive a stack restart.
- **FR-006**: Two runs MUST NOT execute their steps concurrently; an overlapping run MUST wait or
  be refused with a clear message, including runs started from the command line.
- **FR-007**: Concurrent downloads MUST NOT corrupt the raw file store: each download MUST use a
  unique temporary file, and a cached raw file MUST be re-verified against its hash before use.
- **FR-008**: The application images MUST be multi-stage builds that contain no development/test
  dependencies and run as a non-root user; image sizes before and after MUST be measured and
  recorded in the README.
- **FR-009**: Secrets MUST come from environment variables and MUST NOT appear in logs, the UI,
  or committed files.
- **FR-010**: `verify` MUST refuse a source file whose hash differs from the latest run's, MUST
  map malformed input to the same typed errors as ingest, and MUST fetch only the sampled rows
  from the database.
- **FR-011**: Every remaining PR #1 / PR #2 review finding MUST be fixed or have a recorded
  decision.
- **FR-012**: Orchestration logic (step order, failure classification, retry policy) MUST be
  unit-testable without the orchestrator server, and the flow MUST have integration tests
  against a real database.

### Key Entities *(include if feature involves data)*

- **Pipeline Flow**: the ordered steps and their retry policy.
- **Flow Run**: one execution, with status, start/end, per-step outcomes and logs.
- **Schedule**: when the flow runs automatically.
- **Failure Classification**: mapping from the pipeline's error categories to transient or
  permanent.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From a clean checkout, `docker compose up` produces a successful first run on the
  fixture source in under 5 minutes, with no other commands.
- **SC-002**: A transient source failure that recovers is retried and the run succeeds in 100%
  of test runs; a permanent failure is attempted exactly once.
- **SC-003**: Two concurrent runs never produce a corrupted raw file or duplicated data (0
  differences from a single run).
- **SC-004**: The runtime image is measurably smaller than the current image; sizes are recorded
  from real builds.
- **SC-005**: 100% of PR #1 / PR #2 review findings are fixed or have a recorded decision.

## Assumptions

- **User-mandated technology** (PLAN.md): the orchestrator is Prefect (chosen over Airflow as the
  lighter option, per PLAN.md), with its server and UI running as a container in the Compose
  stack.
- A daily schedule is enough for a dataset updated rarely; sub-hourly scheduling is out of
  scope.
- Alerting (email/Slack on failure) is out of scope; failures are visible in the UI and logs.
- Kubernetes deployment is Phase 6 and out of scope; the Compose stack is the target here.
- The full-dataset download remains network-dependent; automated tests use local fixtures and
  simulated failures only.
- Overlap protection may rely on the database (e.g. the existing advisory lock, extended to the
  whole flow) rather than the orchestrator, so command-line runs are covered too.
