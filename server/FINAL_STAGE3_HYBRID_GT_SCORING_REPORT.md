# Final Stage 3: Hybrid GT Internal Scoring and PK Validation

**Status: INTERNAL_PREVIEW_EXECUTED; SCORING_FAIRNESS_NOT_VALIDATED.** Nothing in this report is an official competition result. The Stage 3 service runs on `192.168.51.51:18081` with its own database, identities and audit. Official 8080 and Stage 2 18080 remain separate and unchanged.

## Source and Freeze

The frozen batch `FINAL-STAGE3-HYBRID-GT` was created at **2026-10-19 08:56:20 UTC**. Its private Manifest SHA-256 is `2a84374ba13b7702aa5175b68724748dd6aca0c20c1a7370f1031f0892ab61d5`. The Stage 2 auto-GT source Manifest SHA-256 remains `8e34c6c656cd75c5322d66a4755be991639d029257d41785d395e32dd0bcde62`. Each Stage 3 event retains original candidate IDs, Stage 2 merged-event ID, source class, source Public Manifest SHA and any human review-record SHA.

The only structured human decisions available **at freeze time** were hash-chained review ledgers for SF-004, 006, 008, 011 and 012. At the user's explicit direction, `CHECKED` was interpreted as `HUMAN_ACCEPTED` for this internal test, `CHECKED` with revised fields as `HUMAN_MODIFIED`, and `REJECTED` was excluded. No `HUMAN_MERGED` action was present. Empty reviewer notes do not imply formal GT approval. SF-007 and SF-009 had no review ledger when this batch froze; their Stage 2 `merged_events` were retained as `AUTO_UNREVIEWED`.

| Case | Raw candidates | Human reviewed | Auto-retained scoring events | Rejected | Final scoring events |
| --- | ---: | ---: | ---: | ---: | ---: |
| SF-004 | 3 | 3 | 0 | 1 | 2 |
| SF-006 | 4 | 4 | 0 | 0 | 4 |
| SF-007 | 23 | 0 | 4 | 0 | 4 |
| SF-008 | 4 | 3 | 1 | 0 | 4 |
| SF-009 | 16 | 0 | 6 | 0 | 6 |
| SF-011 | 2 | 2 | 0 | 0 | 2 |
| SF-012 | 1 | 1 | 0 | 0 | 1 |
| **Total** | **53** | **13** | **11** | **1** | **23** |

The 23 scored events comprise 11 `HUMAN_ACCEPTED`, 1 `HUMAN_MODIFIED`, and 11 `AUTO_UNREVIEWED`. SF-004 `ANOM-0002` is in the separate private rejected-audit file and absent from the scored snapshot. SF-008 `ANOM-0003` uses the human-revised type/entity/interval under its original Stage 2 merged-event ID. SF-007 retains 23 raw candidates merged into 4 events; SF-009 retains 16 merged into 6, exactly as in Stage 2.

**Timing boundary:** the human ledgers for SF-007 and SF-009 were subsequently written between 09:02 and 09:21 UTC, after this snapshot had frozen and while the first test runs were under way. They were neither read by this scorer nor backfilled into its stored grades. They are inputs to a separate proposed next batch, not a correction to this one.

## Real Qualifier Runs

50 ran the existing deterministic baseline Agent twice per task with separate Stage 3 team tokens, directories and server-side submission allowances. All six runs queried real Public telemetry, produced a structured answer and one PNG chart, passed Public evidence verification and received `PREVIEW_COMPLETED`. Server UTC open/deadline was shared per task, and all submissions were received inside the window.

| Task / Case | GT events | A predictions / assigned / fully correct | B predictions / assigned / fully correct | A / B score |
| --- | ---: | --- | --- | ---: |
| FT-01 / SF-006 | 4 | 1 / 1 / 0 | 1 / 1 / 0 | 0.5 / 0.5 |
| FT-02 / SF-011 | 2 | 1 / 1 / 0 | 1 / 1 / 0 | 0 / 0 |
| FT-03 / SF-007 | 4 | 1 / 1 / 0 | 1 / 1 / 0 | 0 / 0 |

Both teams' preview base score is **0.5/30**. The `assigned` count is the current matcher's pairing count, **not** a count of completely correct anomaly predictions. The grader supplies type/entity/time/evidence component scores; the private audit has each dimension and the source GT version. All three tasks completed `OPEN -> SUBMISSION_LOCKED -> SCORED -> CLOSED`.

## Four Real-Case PK Rounds

Each round opened one real archived Public Case to two independently authenticated test identities on 50. Both Agents actually ran and submitted; no prerecorded answer was used. The same 30-minute Case, start and deadline applied to both identities. The judge input was **SIMULATED_JUDGE_SCORE**, using the previously exercised Stage 2 *internal test* weights: base 30, PK auto 40, judge 30. No documented committee approval for formal weights was found, so these totals are not formal rules.

| Round / Case | A / B anomalies | A / B charts | Auto A / B | Simulated judge A / B | Composite A / B | Winner |
| --- | --- | --- | ---: | ---: | ---: | --- |
| PK-R01 / SF-004 | 1 / 1 | 1 / 1 | 3 / 3 | 70 / 80 | 33.5 / 36.5 | B |
| PK-R02 / SF-008 | 0 / 0 | 0 / 0 | 0 / 0 | 82 / 74 | 25.1 / 22.7 | A |
| PK-R03 / SF-009 | 2 / 2 | 1 / 1 | 0 / 0 | 78 / 73 | 23.9 / 22.4 | A |
| PK-R04 / SF-012 | 2 / 2 | 1 / 1 | 0.5 / 0.5 | 72 / 85 | 24.1 / 28.0 | B |

Champion continuity was A -> B -> A -> A -> B; all four rounds reached `RESULT_CONFIRMED` and `CLOSED`. PK-R02 honestly produced zero anomalies and zero charts; the chart requirement was **not** satisfied in that round. The current test weights let simulated judge points decide every winner, so the tournament outcome must not be described as an anomaly-scoring comparison.

## SF-009 Event-Level Review

The private event-level artifact is `/var/lib/aiops-private-grader/final-stage3-audit-20261019/sf009-event-level.json` (SHA-256 `499329a2aa9368e2a3a2e6b91577be9d1db9d87317b959903b8b1927142c0ecc`). It records the six frozen GT events with source candidate IDs, entity/type/UTC interval, each team's assigned prediction, four component scores and unmatched items. It is **not** served by the Public API.

For both teams, four SF-009 events were unassigned. The remaining two (EVT-0002 and EVT-0004) were paired to `MetricSeries/container_memory_working_set_bytes` predictions, yet **type, entity, time and evidence each received zero points**. Thus the stored `matched_events=2` is not two correct findings; the correct full-match count is zero. The current matcher also reported zero false positives for those two zero-credit assignments. This is a scoring-accounting defect, preserved here rather than silently changing the frozen batch or replacing its first predictions.

## Recompute, Security, and Regressions

The evaluator re-read all 14 original answers, reverified the submitted Public evidence and recomputed each score **twice** from the same frozen hybrid GT and rubric. Result: **14/14 deterministic matches, zero score mismatches, all Public evidence valid**. The private audit summary SHA-256 is `7af9e2f3b2c8375d6e7db023c08d92eba501b989f99caa2d4e310862edd4fb38`. A zero-anomaly answer on PK-R02 correctly scored zero. Fourteen submissions fall inside their server-side open/deadline intervals; none remains pending.

From 50, A and B could read their own post-lock score breakdown; A reading B's submission returned 404. A Stage 3 token used against Stage 2 (18080) or formal 8080 returned 401. Public feedback omitted GT event count, IDs, source candidate mappings and GT entity/type. The frozen GT directory is `root:aiops-grader`, inaccessible to `aiops-api`. The formal approved-GT directory remains empty.

Post-run database counts: formal `submissions/final_submissions/final_audit = 217/6/13`; Stage 2 `0/14/118`; isolated Stage 3 `0/14/86`. Practice remains CLOSED and Semifinal AM/PM remain NOT_OPEN. The 50 Web services on 8088/8089 both returned 200. No historical Stage 1/2 submission, GT file or leaderboard was changed.

## Blocking Findings Before a More Realistic Batch

1. **Taxonomy mismatch:** 15 of the 23 frozen scoring events use production-side types absent from the contestant's 11-type enum. Type credit is structurally unattainable on those events. Prefer human-reviewed mapping into the existing enum; do not expand the enum merely to make this batch score higher.
2. **False-positive accounting:** eight assigned prediction/GT pairs across FT-02, FT-03 and PK-R03 received zero type/entity/time/evidence credit yet were counted as matches, suppressing false-positive penalties. A versioned matching fix and a new batch are required; do not rewrite stored v1.0 grades.
3. **Late human review:** SF-007 and SF-009 decisions arrived after freeze. Their excessive or sub-second automatic events must be reviewed and selected in a new snapshot, not retrospectively spliced into these 14 first-run results.
4. **Workflow realism:** both test identities ran on the same 50 host and source IP, and judge scores were simulated. PK-R02 produced no chart. The 30/40/30 weights are a Stage 2 internal test configuration, not documented formal approval.
5. **No full correct prediction:** all 14 answers had zero matches receiving positive type, entity, time **and** evidence credit together. This is an Agent/GT alignment finding, not proof that the Case data is invalid.

The Stage 3 internal workflow and archive are reproducible; **fair Final scoring is not validated**. A follow-up batch must be preregistered with the latest human review, a small evidence-backed SF-007/SF-009 event set, canonical types from the existing 11 enums, and a corrected matching rule before any accuracy or winner claim.

### Post-run shadow check

An isolated `final-stage3-hybrid-preview-1.1-candidate` scorer was tested without deployment to the active batch. It refuses to assign a GT event to a prediction receiving zero type, entity, time **and** evidence credit. Its 14-answer shadow run adds eight false positives (FT-02: 2; FT-03: 2; PK-R03: 4), with **zero changed scores** because those particular original scores were already clamped at zero. The private artifact is `/var/lib/aiops-private-grader/final-stage3-audit-20261019/strict-v1.1-shadow.json`, SHA-256 `0d334781bd8754bb48f31445c15b53655b737648bdee6bcd1de05944961b631c`. Original scores, predictions, GT and winners remain unchanged; v1.1 is a candidate for a new preregistered batch only.
