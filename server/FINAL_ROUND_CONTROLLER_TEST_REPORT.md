# Final Round Controller Test Report

Status: `SIMULATION_ONLY`. The production Final catalog and all 12 task/round rows remain `NOT_OPEN`.

The new persistent controller stores tasks, rounds, submissions, judge scores, and append-only audit entries in separate tables of the existing grading SQLite database. FT transitions are `NOT_OPEN -> OPEN -> SUBMISSION_LOCKED -> SCORED -> CLOSED`. PK transitions are `NOT_OPEN -> READY -> RUNNING -> SUBMISSION_LOCKED -> JUDGING -> RESULT_CONFIRMED -> CLOSED`. Every transition is checked under a SQLite write transaction. PK configuration requires two different participants; a later challenger must face the previously confirmed winner. Non-simulation rounds reject reuse of an already used Case. An official result cannot be confirmed while weights remain unlocked.

The isolated replay is saved at `/var/lib/aiops-final-stage1-simulation/replay-20261019/eight-round-replay.json` on 51. It used synthetic answers/scores, no Private GT and no production leaderboard. Each round had two synthetic submissions, one judge score per team, result confirmation, close, and a persisted audit record.

| Round | Simulated Case | Champion before | Challenger | Winner |
| --- | --- | --- | --- | --- |
| PK-R01 | SF-004 | TEAM-01 | TEAM-02 | TEAM-02 |
| PK-R02 | SF-008 | TEAM-02 | TEAM-03 | TEAM-03 |
| PK-R03 | SF-009 | TEAM-03 | TEAM-04 | TEAM-04 |
| PK-R04 | SF-012 | TEAM-04 | TEAM-05 | TEAM-05 |
| PK-R05 | SF-004 | TEAM-05 | TEAM-06 | TEAM-06 |
| PK-R06 | SF-008 | TEAM-06 | TEAM-07 | TEAM-07 |
| PK-R07 | SF-009 | TEAM-07 | TEAM-08 | TEAM-08 |
| PK-R08 | SF-012 | TEAM-08 | TEAM-09 | TEAM-09 |

R05-R08 reuse is confined to the simulation database. Production R05-R08 stay `AWAITING_CASE`; these are not eight distinct formal cases.

Regression coverage includes: early access denial, source-IP-bound organizer preview, two teams seeing the same PK Case while a third is denied, own-submission-only access, no pre-confirmation grade disclosure, idempotency conflict, one valid submission per task/team, rejected duplicate transitions and confirmations, champion continuity, eight-round replay, judge correction audit, grader-pending restart recovery, and refusal to confirm a formal round. The 51 candidate test suite passed with three pre-existing skips. HTTP 429 was observed during rapid 50 polling; the organizer test adapter now waits instead of disabling the limiter. The five real 50-Agent runs are **single organizer-identity previews**; physical two-host team isolation and judge UI acceptance remain for the pre-release rehearsal.
