# PR review follow-ups carried into 004 (FR-007, FR-010, FR-011)

Source: automated reviews on PR #1 (review 5558342670) and PR #2 (review 5558345373).

| # | PR | Location (at merge) | Finding | Spec |
|---|----|---------------------|---------|------|
| F1 | 1 | `src/football_stats/extract.py:118` | `.partial-<pid>` collides across containers (all PID 1, shared `rawdata` volume) | FR-007 |
| F2 | 1 | `src/football_stats/extract.py:139` | cached `matches-<sha>.csv` reused without re-hashing | FR-007 |
| F3 | 1 | `src/football_stats/verify.py:128` | `verify --source-file X` not checked against latest run's sha | FR-010 |
| F4 | 1 | `src/football_stats/verify.py:130` | only `FileNotFoundError` mapped; non-UTF-8 / csv.Error / ragged → exit 1 | FR-010 |
| F5 | 1 | `src/football_stats/verify.py:115` | fetches all ~240k matches to check a 20-row sample | FR-010 |
| F6 | 1 | `migrations/versions/0001_initial_schema.py:92` | no index on `matches(source_sha256, source_row_number)` (verify, 002 convergence) | FR-011 (measure first) |
| F7 | 1 | `src/football_stats/pipeline.py:121` | `counts_by_reason()` computed three times | FR-011 |
| F8 | 2 | `src/football_stats/load.py:155` | convergence is per source file; violators from older files remain (documented assumption) | FR-011 (README note or verify check) |
| F9 | 2 | `src/football_stats/data_contract.py:253` | Python loop over all pandera failure cases | FR-011 |
| F10 | 2 | `src/football_stats/load.py:185` | `.encode()` on SQL strings, inconsistent style | FR-011 |
