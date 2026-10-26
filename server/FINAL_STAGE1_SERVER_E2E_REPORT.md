# Final Stage 1 Server and 50-Agent E2E Report

**Decision: `FINAL_STAGE1_INTERNAL_PREVIEW_E2E_PASS`; formal Final release remains blocked.** This phase deployed an internal Final task API, persisted controller, submission protocol, Private preview grading, and an isolated eight-round simulation on 51. No Practice/Semifinal/Final session was opened; no GT was approved; no official leaderboard or historical score was changed.

## 1. Candidate Case delivery

The Master handoff `/root/ops-competition-2026/plans/SF-003-to-SF-012-master-ready-handoff-20261018.json` listed all seven selected cases as ready Public packages with draft GT. The authorized Mac SSH relay copied only each six-file Public directory to 51's root-only staging. `validate_exchange_v2.py` checked the exact file whitelist, source Manifest and per-file SHA-256, row counts, time window, Trace Index and Public/private leak conditions. `prepare_exchange_v2.py`, the existing importer `--validate-only`, and `validate_case.py` all passed. Existing SF-001/002/003/005/010 were not copied or reimported.

| Case / attempt | Master Public Manifest SHA-256 | Metrics / Logs / Spans | 51 status |
| --- | --- | ---: | --- |
| SF-004 / 05 | `7074bed09aaae6f5828f8e37e8c03aad0b6dc9caa0188f386867649c9047b242` | 39,000 / 196,371 / 349,727 | Imported, validated |
| SF-006 / 01 | `895d85aedfc019b2573a573ca848f36d236047f66d4c279710ca8df495cee66f` | 39,000 / 199,152 / 346,841 | Imported, validated |
| SF-007 / 08 | `b5b3291da7ebc0f8b54f2098ffb3193f763c657f99c2843552c23562a5b7493d` | 40,752 / 190,786 / 339,854 | Imported, validated |
| SF-008 / 09 | `5182f70f54aa264746db2527f7da5b0a36d40f0328e1ab6003963b8cc7a59418` | 40,560 / 187,619 / 346,964 | Imported, validated |
| SF-009 / 04 | `85c2d7f401939d62ad2cf76d24e19c5cb5250ad76c31e775293520f33a6bfd37` | 40,734 / 195,753 / 354,327 | Imported, validated |
| SF-011 / 05 | `025b1a41fc56c17e5b0dd9e36731260eaa5268696b5f0e3b8d36b0a9af12d47e` | 40,792 / 192,786 / 349,397 | Imported, validated |
| SF-012 / 04 | `7e7306cf61205d91f5e9c34dea33bc86fc077e0c3bb25e1bce15b684be6bbd12` | 40,560 / 190,092 / 351,032 | Imported, validated |

Each is a 30-minute UTC archived Public window. The importer stable-sorted/re-encoded Parquet and created a new 51 Manifest containing the unchanged Master source Manifest SHA. New case files are `root:aiops-api` with read-only `0440`; original transfer staging remains root-only. The actual 51 locations are `/data/aiops-contest/public/cases/SF-*/public-v1/` and `/data/aiops-contest/public/manifests/SF-*.json`. From the bound 50 organizer identity, all seven tasks returned nonempty Metrics, Logs and Traces over real HTTP; an SF-006 Trace ID expanded to six real spans.

Seven draft files were separately copied to `/etc/aiops-private-grader/final-gt-preview/SF-*.json`. Each 51 SHA-256 matched its Master draft. All seven remain `DRAFT_PENDING_HUMAN_REVIEW`; `/etc/aiops-private-grader/final-gt/` has zero approved files. `aiops-api` cannot read a draft; `aiops-grader` can. The Private Grader is bound to `127.0.0.1:9001`, unreachable from 50.

## 2. Task catalog and control

The internal catalog maps FT-01→SF-006, FT-02→SF-011, FT-03→SF-007 and PK-R01→SF-004, R02→SF-008, R03→SF-009, R04→SF-012. Formal PK-R05...R08 have no Case (`AWAITING_CASE`). The public task prompt asks for autonomous investigation and discloses only the full Case query window, not injection timing, mechanism, target, root or expected anomalies. The new `app/final_store.py` persists the three FT and eight PK states, answer receipts, judge revisions and audit entries in new tables of the existing grading SQLite database. `scripts/manage_final_stage1.py` is the root-only offline organizer control; in this phase it rejects production mutations and permits only a separate `--simulation` database.

## 3. 50 Agent and scoring preview

The organizer-only adapter on 50 at `/opt/aiops-competition/organizer-final-stage1/run_final_preview.py` reused the existing baseline Agent's Public collection, detectors and evidence generation. It did not change the participant Starter. The five recorded runs used the 50-bound organizer credential and never read GT. Each answer went through the 51 Final submission Schema, server-side Public evidence verifier and Private preview grader. HTTP POST returned 202. Values below are **draft-GT preview only**, on a 10-point per-task scale; they must not appear on an official board.

| Task | 50 run ID | Anomalies / chart | Submission ID | Preview result | Agent runtime |
| --- | --- | ---: | --- | ---: | ---: |
| FT-01 | `FT-01-56a762cc50` | 1 / 0 | `FSUB-1C9AB25D115A4D3BA057` | 0.500 (rubric 1.0) | 6.673 s |
| FT-02 | `FT-02-0cab4397b4` | 1 / 0 | `FSUB-3EDAB09EEA364E9299DF` | 1.000 (rubric 1.0) | 6.863 s |
| FT-03 | `FT-03-2a96b3231c` | 1 / 0 | `FSUB-B95FDC76A41F4B6E84EE` | 0.087 (rubric 1.0) | 6.499 s |
| PK-R01 | `PK-R01-47c0c1d62a` | 1 / 0 | `FSUB-1859FED61011477C81DC` | 2.000 (rubric 1.0) | 6.705 s |
| PK-R02 | `PK-R02-596e6a24c8` | 0 / 1 | `FSUB-DD26A38D592E4190BA46` | 0.000 (rubric 1.1) | 7.525 s |

The three FT previews total **1.587/30** for this one organizer baseline Agent run; this is neither nine-team scoring nor a valid comparison of official contestant performance. PK-R02's chart was drawn from real Public Metrics, with time-series data, PNG bytes and SHA-256 accepted by the Final submission endpoint. An empty anomaly list correctly scored zero. Rubric 1.1 tightened evidence relevance after the first four preview grades; their original grades and versions were preserved.

The first FT-01 grade poll encountered a route-order 404 after the answer had been accepted. The route order was fixed and the same Submission ID subsequently returned `PREVIEW_COMPLETED`; no rerun replaced it. A later FT-03 poll hit the existing HTTP 429 limit after its answer had been accepted; the original Submission ID was recovered. The 50 organizer adapter now waits on 429, without changing server limits.

## 4. PK simulation, isolation and regression

The isolated replay at `/var/lib/aiops-final-stage1-simulation/replay-20261019/eight-round-replay.json` completed eight two-team rounds with synthetic preview scores and judge inputs. It recorded eight result confirmations, eight champion changes and final simulated champion TEAM-09. R05–R08 reused the four authorized PK Case IDs **only** in that simulation database; the production catalog still has no Case for them. No synthetic result entered the production leaderboard. A test with separately IP-bound synthetic teams confirmed both participants can read the same Case while a third cannot, each team can read only its own submission, and scores remain hidden until result confirmation. Actual two-machine participant isolation remains pending the cloned team hosts.

The full 51 candidate regression suite passed (three pre-existing skips), including the existing semifinal and grading tests. Production API and Private Grader remained healthy; anonymous Final tasks and submissions returned 401, and the pre-existing semifinal anonymous route still returned 401. Practice remains `CLOSED`; semifinal AM/PM remain `NOT_OPEN`; no Final row was opened. The competition state and Team Registry file mtimes predate this deployment. Existing historical QA and anomaly submissions were not modified.

## 5. Remaining pre-release work

1. Human-review and explicitly approve each observable-anomaly GT; no draft may produce official scores.
2. Register four distinct PK Cases for R05–R08 and audit their freshness before any formal publication.
3. Confirm and lock Final task weights, judge rubric, optional RCA bonus, tie-break and correction procedure. The current PK scorer deliberately refuses formal confirmation.
4. Clone real team hosts and verify two separate source IPs, independent Agent processes, network interruption and concurrent deadline behavior. The synthetic tests do not substitute for this physical check.
5. Decide the official chart/presentation evaluation protocol. Current chart verification checks structure and integrity, not visual quality.
6. Run a controlled release rehearsal with the nine designated FINAL team identities only after the above approvals. The ordinary Team Registry currently grants no FINAL permission.

Artifacts: `FINAL_API_CONTRACT.md`, `FINAL_SCORING_DESIGN.md`, `FINAL_ROUND_CONTROLLER_TEST_REPORT.md`, `grading/schemas/final-submission-v1.schema.json`, and `reports/final-stage1/openapi.json`. The 51 deployment backup is `/opt/aiops-contest-server/backups/final-stage1-20261019/`.
