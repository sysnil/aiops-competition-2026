# Final Stage 1 API Contract

Status: `INTERNAL_DEVELOPMENT_ONLY`. Base URL: `http://192.168.51.51:8080`. The existing Practice and Semifinal routes remain unchanged. This document describes the new Final Stage 1 routes; `openapi.json` in the same report directory is the generated OpenAPI snapshot.

All routes require `Authorization: Bearer <team-token>`. The server verifies the token, its assigned source IP, and Final authorization. The dedicated organizer test identity on 50 can preview closed tasks from its bound IP. Ordinary teams cannot use that preview path and currently have no FINAL mode permission. Server UTC time controls every open and deadline check.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/v1/final/tasks` | Visible tasks only. |
| GET | `/v1/final/tasks/{task_id}` | Public prompt, Case window, modalities, submission limit. |
| GET | `/v1/final/tasks/{task_id}/case/metadata` | Archived Public Case metadata. |
| GET | `/v1/final/tasks/{task_id}/case/topology` | Archived Public topology. |
| GET | `/v1/final/tasks/{task_id}/case/{metrics,logs,traces}` | Public data, with UTC `start`/`end`, entity filters and pagination. |
| GET | `/v1/final/tasks/{task_id}/case/traces/{trace_id}` | Waterfall spans. |
| POST | `/v1/final/tasks/{task_id}/submissions` | Idempotent answer receipt; returns HTTP 202 and a `FSUB-...` ID. |
| GET | `/v1/final/submissions/{submission_id}` | This team's own submission and permitted feedback. |
| GET | `/v1/final/pk/rounds/current` | Current authorized running round. |
| GET | `/v1/final/pk/rounds/{round_id}` | Participant round status; winner only after confirmation. |
| POST | `/v1/final/pk/rounds/{round_id}/submissions` | PK alias for the same answer protocol. |

The older `/v1/final/rounds/...` routes remain available for backward compatibility and use the legacy controller. The new eight-round controller is under `/v1/final/pk/rounds/...`. The organizer control surface is the root-only offline `scripts/manage_final_stage1.py` on 51. Stage 1 forbids formal state mutation; it accepts mutations only with `--simulation` and a separate SQLite database.

The answer envelope has `idempotency_key`, `answer_sha256`, and `answer`. The digest is SHA-256 of the exact JSON `answer` object serialized with sorted keys and compact separators. The generated schema is `grading/schemas/final-submission-v1.schema.json`. A minimal valid answer has `task_type=FINAL_ANOMALY_INVESTIGATION`, `schema_version=final-1.0`, `task_id`, `case_id`, `run_id`, `agent_status`, `anomalies`, `charts`, and `summary`. Each anomaly reuses the v3 observable-anomaly type, entity, UTC interval and Public evidence-record format. `root_hypothesis` is optional. Empty anomalies are allowed but score zero against nonempty reviewed GT. Charts use the existing line-chart/PNG artifact model; a chart's PNG hash is checked at submission. The organizer 50 adapter generated and submitted a real Metrics chart in PK-R02.

Example read with a protected token already provisioned on the caller:

```bash
curl -H "Authorization: Bearer $AIOPS_TEAM_TOKEN" \
  'http://192.168.51.51:8080/v1/final/tasks/FT-01/case/metrics?page_size=100'
```

Responses never contain fault type, injection target or interval, expected anomalies, Private GT, or answer keys. Before a formal task opens, ordinary teams receive 404 for its detail and data. Before a PK result is confirmed, participants can read their own submission status but not an opponent's answer or score. Repeating an idempotency key with a different answer hash returns 409; a received answer survives worker restart. Nginx permits POST only on the two exact Final submission route patterns and retains the existing Team/IP limits.
