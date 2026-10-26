# 51 submission and grading evidence viewer

Status: DEPLOYED_AND_VERIFIED on 2026-10-20.

2026-10-21 repair: the viewer now reads an atomic SQLite-backup snapshot,
refreshed every five seconds by `aiops-submission-snapshot.timer`. This avoids
the live WAL database needing to initialize sidecars inside the viewer's
read-only mount. The original scoring database and answer archives remain
read-only to the viewer. See `reports/SQLITE_READONLY_FIX_20261021.md`.

## Access

- Service: `aiops-competition-submissions.service`, Streamlit on **51 loopback `127.0.0.1:8504`**. It is not proxied by Nginx and is unreachable on `192.168.51.51:8504`.
- From a committee workstation: `ssh -L 18505:127.0.0.1:8504 root@192.168.51.51 -N`, then open `http://127.0.0.1:18505/`.
- Login uses the existing committee review access code. Only its PBKDF2-SHA256 verifier was copied to `/etc/aiops-submission-review/auth.json` (0640 `root:aiops-api`); the code is not stored in the Web project or this report.

## Evidence

- The read-only table combines `/var/lib/aiops-grading/submissions.sqlite3` `submissions` and `final_submissions`, with filters for competition stage, team, Case, status, grading mode and submission/task ID. It preserves zero scores and leaves unscored records blank.
- Selecting a row opens the exact participant answer JSON, its canonical SHA-256 check against the database, file SHA-256, grading status, score and available grading/evidence-validation record. The verified answer can be downloaded. Opening a file writes only a viewer audit entry under `/var/lib/aiops-submission-review/`.
- Practice/Semifinal answers were already files in `/var/lib/aiops-grading/answers/`. Final answers were already stored in SQLite; new Final submissions now also write once to `/var/lib/aiops-grading/final-answers/<submission_id>.json` (0640 `aiops-api:aiops-api`). Existing Final answers were backfilled without changing database rows.
- Deployment audit: **223/223** current answers verified against database hashes: 217 ordinary files and 6 Final files. The Final `(submission_id, answer_sha256)` row digest remained `bf9785afac20a54219ee47d65bcee320625f871fa4dc09b17707bc27312e2fc1` before and after backfill.

## Isolation and tests

- The service runs as `aiops-api` with the grading directory mounted read-only. Its mount namespace hides `/etc/aiops-private-grader`; it cannot write the scoring SQLite. Existing Public visualization, private review, API, and scoring services remained active.
- The 51 API/grading suite passed (three existing skips). Viewer data/auth tests passed. Browser QA passed against a synthetic submission: locked screen concealed the record, authenticated table selection opened JSON and score evidence, and download was available. Production locked-screen QA passed without exposing records.
- Screenshots: `reports/isolated-answer-open.png` shows **synthetic** data; `reports/production-locked.png` shows the real production login screen. No real participant answer was used in screenshots.

The Web itself never reads Private GT or modifies answers, scoring, task state or competition sessions. The 51 scoring database remains the authoritative score source.
