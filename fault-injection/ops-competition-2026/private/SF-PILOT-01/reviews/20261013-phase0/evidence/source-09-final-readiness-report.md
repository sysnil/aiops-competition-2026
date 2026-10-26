# Stage5-v3 Final Fast-Ready: no-fault execution result

**Final state: `ENGINEERING_PARTIAL`; `FORMAL_QUALIFICATION_ALLOWED=false`.**
The mounted data disk, complete no-fault raw lifecycle, three-phase timed
Trace capture, network probes and two-worker archival all operated on real
data. The source-locked quality assessor failed structurally, the unchanged
old Clean CPU/p95 rules failed in a separate diagnostic replay, the sole
Collector candidate missed its full-window timed read, and NetworkPath still
lacks two qualified normal controls. This run did not inject a fault, create
a formal Case, access TEST GT, run an RCA prediction or freeze Stage5-v3.

| Required status | Decision |
| --- | --- |
| `RUNNER_STATUS` | `RUNNER_RAW_LIFECYCLE_PASS_PREREG_SCORER_FAILED` |
| `COLLECTOR_RELIABILITY_STATUS` | `COLLECTOR_RELIABILITY_INCONCLUSIVE_TIMED_CAPTURE_FAIL` |
| `CLEAN_PROTOCOL_STATUS` | `CLEAN_NOT_VALIDATED_PREREG_SCORER_FAILED_OLD_GATES_FAIL` |
| `SERVICE_READINESS` | `SERVICE_CONDITIONAL` |
| `POD_READINESS` | `POD_CONDITIONAL_WORKLOAD_COVERAGE` |
| `NODE_READINESS` | `NODE_PENDING_SAFETY_CONTRACT` |
| `NETWORK_PATH_READINESS` | `NETWORK_CONTROL_BLOCKED` |
| `FORMAL_QUALIFICATION_ALLOWED` | `false` |

The Master disk is `/dev/sdb1`, ext4 UUID
`a35051a2-3d09-4b83-bbc0-3bb055e8a4f3`, mounted at `/data/stage5-v3`.
The S1 raw run lives at
`/data/stage5-v3/final-fast-ready/runs/S5V3FFR-CLEAN-20261013-S1/`:
2,028 files and 4,726,583,363 bytes. Final free space: data disk
307,720,200,192 bytes; root disk 46,204,506,112 bytes versus the unchanged
46,000,000,000-byte floor. The v2 archive source manifest SHA-256 is
`74bd6c42ee4e9999e62b288eda84bc99a3d035835d624be29afd96539579d69b`;
Worker2 and Worker3 each matched every source file and passed deterministic
restored reads. The original verifier failure and manifest are preserved.

The only Collector change was `spike_limit_percentage: 25 -> 15`, under an
independent systemd rollback timer. Over 30 minutes, 120/120 monitor points
showed gateway gRPC receiver refused 0→0; the limiter refused metric was
**UNKNOWN**, not zero. All 21,309 issued Trace IDs appeared at the Proxy in
the preserved OpenSearch read, but that first read completed around +103s,
past its +60 deadline. The candidate was rolled back to 25. A temporary
postrollback node-metric gap was observed, followed by a fresh 21/21-point
ten-minute recovery watch and Global Safety PASS. No additional Collector
parameters were tried.

The S1 no-fault Clean used seed 864101 and unchanged 1.4 sessions/s,
20/30/50 workflows, Image 1/s, Cart 2/s. Before/During/After produced
51,920 real traces, 354,179 live spans and 244,964 logs. All 300 scheduled
W2→W3 network probes returned valid measurements. Candidate CPU Safety v2
sampled 50/50 PASS; three segmented Trace Completion v2 phase assessments
passed, with eight +240 noncritical control-plane additions. These shadow
results are **not approved protocol replacements**. The original scorer
rejected valid `window` extension fields before scoring. The independent
diagnostic replay found old CPU increments +0.259903 and +0.295007 core on
Worker1/Worker2 and Cart's maximum p95 ratio 1.393× across three consecutive
high minutes: both old gates fail. Candidate B/E also fail. S2 was not run.

The W2→W3 path was freshly re-proven with EndpointSlice, Calico route and
dual-ended ordinary business packets under a short same-policy preflight.
Image Server Spans lacked direct Pod UID; Payment W1 met a minimum 31 linked
Spans per minute but exceeded the old p95 rule once and its independence from
the shared Checkout source under fault remains unknown. Service and Pod
identity/resource evidence is useful but fault qualification remains undone;
Node CPU contention waits for an approved during-fault safety contract.

Final Global Safety is `GLOBAL_SAFETY_PASS` with zero blockers; five-host
residue is `HOST_RESIDUE_PASS`. All this round's study, probe, receiver and
rollback timers are inactive. The next step is human review of the corrected
versioned scorer, CPU/Trace candidate contracts, Node safety stops and
NetworkPath normal controls. The first source-locked scoring failure and
all original data remain intact.
