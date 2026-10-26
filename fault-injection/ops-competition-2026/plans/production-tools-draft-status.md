# Production tooling — DRAFT, not an executed case

The `competition_case_*`, `prepare_competition_case.py`, `run_competition_case.py`,
`validate_contest_capture.py`, `contest_exchange_schema.py`, `export_contest_hour.py`
and `draft_observed_anomalies.py` files are preparatory code only. No one-hour
case has been registered or executed with them. Syntax/helper tests do not
constitute a live end-to-end validation or server import compatibility proof.

The independently preserved qualification adapter and its source lock are not
changed by this draft. Production registration, controller and observer must
reject `COMPETITION_PRODUCTION_TELEMETRY_BLOCKED`.

Observed Collector refusals and absent Proxy spans require a separate repair
and validation version. Do not turn the blocked evidence into a PASS by editing
the record, loosening a threshold, or replacing qualification captures.

The proposed six fixed ten-minute collection chunks keep the qualified Service
stall inside one chunk. All immutable read deadlines still require live
validation before this draft can be claimed complete. Proposed annotation code
reads only public observations and emits human-review drafts, never scoring GT.

Parquet helper dependencies were installed only into a temporary Mac test
directory; Linux wheels were downloaded but not installed on the lab hosts.
