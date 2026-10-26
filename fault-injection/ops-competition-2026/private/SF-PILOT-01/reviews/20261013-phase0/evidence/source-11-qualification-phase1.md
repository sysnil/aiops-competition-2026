# Stage5-v3 Mechanism Qualification Phase 1

**Decision: PREFLIGHT_BLOCKED_ZERO_FAULTS.** Two independent, full-length no-fault qualification windows were archived. Both failed the pre-fault workload gate, so the first fault batch was stopped before registration or injection. No mechanism is upgraded to READY. This is a rehearsal audit, not a Stage5-v3 DEV/TEST case or an RCA accuracy result.

## Scope and immutable evidence

All new work resides in `/root/stage5-v3-mechanism-qualification/`. Stage5-v1/v2 frozen assets were read only; Stage5-v2 TEST was not accessed. Stage5-v3 DRAFT documents were not changed or frozen. No Deployment, OTel, Prometheus, Jaeger, OpenSearch, or network configuration was modified. Temporary no-fault iperf receivers were stopped and five-host residue audits passed. No RCA method or formal blind prediction ran.

The intended first batch was SERVICE process stall, POD recommendation memory pressure, NODE Worker1 CPU contention, and NETWORK_PATH W3→W2 loss, each with two seeds. **None of these eight fault experiments was executed.** The proposed fault seeds and targets were not frozen because the prerequisite no-fault window failed. The separate clean-window seeds `851100` and `851110` are not fault-rehearsal repeats.

| Evidence | SHA-256 |
|---|---|
| `results/qualification-summary.json` | `7123c6b07154b962bf2eeb0531248eb893a5cf3acb5d6a8348cd2978381e2bf5` |
| `results/clean-decision-v1.json` | `3ce230b149d0524d68676dbd659c9a864290446e4654174cef9b12835a0b1121` |
| `results/clean-decision-v2.json` | `c582628dbe528ba5c8870556006b79944ac45f602b1b6b8cc0de7e23360586bf` |
| `results/clean-analysis-v2.json` | `ada6cab9be48defea605843df44bf1dcabf26f3aff81108b75ab16dbe5f8e0ec` |
| `runs/MQP1-CLEAN-V2/route-proof.json` | `11ce5d33b7fa3a059d9ee2c04efcbb8ca298cee22587e26c2d193890f03b6b9d` |
| `results/v2-public-boundary-audit.json` | `5d850088b3f64297b8c4dea3443765e6e191c2b1d268f754581a13020e31efd9` |
| `snapshots/v1-product-503-trace.json` / `snapshots/v2-product-503-traces.json` | `3516be807688e3d7e9fac2788ef4d0b850cc860ed937b2da914a55f5562b65a2` / `b757804ace846c882dfd132644107a1f924fcb6d9ee6857090e9a771d3ee7dbb` |
| `snapshots/health-final.json` / `snapshots/five-host-residue-final.json` | `f3644826b3d484919b5855447bbdb7e7740de34ef5be507ce9213d6feaba654f` / `c02164da886e527a472c0c5b0d36977ff6206fb10428994329664530661ad7ec` |

## Current topology and base health

Fresh Kubernetes Pod, Service, EndpointSlice, Deployment, and Node JSON snapshots are in `snapshots/`. All four nodes were Ready; Calico had 3/3 BGP peers on each node; Prometheus was Ready, OpenSearch green, Jaeger OpenSearch storage had recent frontend spans, and the Collector was Ready. The final health gate recorded approximately 284 existing load-generator requests in two minutes with zero nonzero failures; this gate is distinct from the newly generated state-machine requests below. The five-host residue audit was PASS on Master, Worker1/2/3, and router; Worker2/3 qdisc roots were default `mq`, with no active netem/stress/fault unit. This confirms **post-cleanup cluster health**, not clean workload qualification.

| Service | Current placement and identity | Qualification implication |
|---|---|---|
| Frontend | Worker1 `frontend-68d8bbfdc8-c4n85` UID `6305d17a-27f0-4dbf-8fc7-7238acb3be91`; Worker2 `frontend-68d8bbfdc8-8mnzh` UID `d4bb49b4-6fb8-4a07-aaf6-e2caff5df974` | Phase 4's former two-Frontend-on-Worker2 control mapping is obsolete. |
| Recommendation | Both Worker3: `recommendation-c887f6f6b-cxb5j` UID `8b7c229b-cea1-440f-bc88-e9c1f246ef9e`; `recommendation-c887f6f6b-zchkk` UID `3886c989-d7a1-44e9-8b44-64c536db1897` | Both receive business calls, but the weaker replica did not meet the per-minute threshold in either clean version. |
| Checkout → Cart | Checkout Worker3 UID `0735b911-c141-463e-8575-429a7673d678` → Cart Worker2 UID `4fdedc27-dc56-4272-9f30-4c2a47832c9a` | Current route to `10.244.126.0/26` via Worker2 and a payload-bearing Checkout→Cart packet establish W3→W2 membership. |
| Ad / Payment | Ad on Worker1+Worker3; Payment on Worker1+Worker2 | The initial ten-minute trace sample still concentrated ordinary Ad and Payment server spans in one replica each; neither is used as a two-Pod comparison target in this batch. |

The v2 live route audit verified **7/7** current business edges with Pod UIDs, route and payload-bearing packet: Checkout→Cart W3→W2; Frontend-proxy→Image-provider local W2; Frontend W1/W2→ProductCatalog W3; Frontend W1/W2→Cart W2; Checkout→Shipping W3→W1. Image and ProductCatalog were candidate nontraversing controls for W3→W2; actual control stability is assessed separately below. A route proof is not a fault result.

## Full no-fault qualification windows

| Rehearsal | UTC Before / During / After | Live Metrics/Logs/Traces/Kubernetes | W3→W2 probe | Workload qualification |
|---|---|---|---|---|
| `MQP1-CLEAN-V1`, seed 851100 | 13:23:01–13:33:01 / 13:33:01–13:38:01 / 13:38:01–13:48:01 | Pipeline PASS; spans 141,976 / 69,439 / 141,834; logs 102,093 / 49,892 / 100,750; first-read completion lag 54 / 34 / 49 s; immutable +60 s supplement had no late spans | 120/120, 60/60, 120/120 SUCCESS; no timeout or miss | **BLOCKED**: one product-detail 503; Recommendation replica coverage 6–27/min; cart control p95 exceeded 1.25 twice (about 1.31–1.32). |
| `MQP1-CLEAN-V2`, seed 851110 | 13:56:15–14:06:15 / 14:06:15–14:11:15 / 14:11:15–14:21:15 | Pipeline PASS; spans 164,709 / 79,439 / 167,721; logs 107,559 / 51,787 / 107,990; first-read completion lag 53 / 35 / 53 s; +60 s supplement added **one** After span without overwriting first capture | 120/120, 60/60, 120/120 SUCCESS; median receiver loss and independent ICMP loss 0 in all three phases | **BLOCKED**: two product-detail 503s; weaker Recommendation replica had 26 Server Trace IDs in one Before minute (<30); Product-detail control p95 ratio reached 1.2514 in minute 10 (>1.25). |

Both runs used the real state-machine workload, fixed-frequency ordinary image/product/cart requests and the same 10+5+10 window lengths. v1 and v2 are **different workload revisions**, not two repeats of one fault protocol: v2 made the already verified product→recommendations branch deterministic while leaving the MEDIUM arrival profile and 20/30/50 workflow weights unchanged. Their data and decisions are separate. The v2 changed branch increased Recommendation traffic, but did not meet the frozen-before-fault draft threshold on every minute. The v2 Checkout→Cart Before last-five-minute verified trace counts were `[46, 53, 50, 45, 48]`, all above 30; this is target-path traffic evidence only. Image control had at least 60 successful requests and 59 verified edge Trace IDs per minute with stable p95. Product-detail had at least 119 successful requests and 119 edge Trace IDs per minute, but its p95 ratio `1.2513845534` at minute 10 exceeds the proposed 1.25 limit. It was **not rounded down or accepted by exception**.

The three product failures are real client outcomes, not missing telemetry. V1 had one `/api/products/OLJCESPC7Z` 503; v2 had `/api/products/0PUK6V6EV0` and `/api/products/1YMWWN1N4O` 503s. Each corresponding raw Jaeger trace contains `frontend-proxy` ingress HTTP 503 and frontend egress `error.reason=connection termination`, with no ProductCatalog span. The repeated pattern needs a separate pre-fault infrastructure/workload diagnosis; it was not added to the clean-baseline exception list. The final cluster health gate can be PASS while this stricter generated-workload gate is BLOCKED.

The initial and final public-boundary scans found no private root, injection-command or fault-target field in 713 (v1) and 795 (v2) candidate telemetry files. This is a rehearsal leak screen, not a frozen Stage5-v3 public-bundle audit. The normal control and Node activity analyses use actual traces; Shipping server spans lack `k8s.pod.uid`, so Worker1 Shipping activity is explicitly marked **unique-Service-to-Pod public-topology inference**, never direct Trace UID attribution.

## Candidate mechanism decisions

The table reports **Phase 1 precondition** outcomes. No Injection Validity, fault-specific Observability Validity or Business Impact label can be computed because no fault was injected. In particular, no-fault user impact is **not** a mechanism `NONE` label.

| Layer | Mechanism | Seed 1 | Seed 2 | Direct root evidence | Normal control | Recovery | Qualification decision |
|---|---|---|---|---|---|---|---|
| SERVICE | Service-wide Recommendation process stall | NOT_RUN; no fault seed frozen | NOT_RUN | NOT_EVALUATED; target two-Pod Before counts failed ≥30/min | Image/product normal streams observed; product p95 exceeded 1.25 in v2 | No fault; both clean windows recovered and five-host PASS | `BLOCKED_PRECONDITION`; prior v3 mechanism remains CONDITIONAL, **no READY suggestion** |
| POD | Recommendation single-Pod memory pressure | NOT_RUN | NOT_RUN | NOT_EVALUATED; target/sibling Before comparison failed ≥30/min on weaker replica | Sibling UID/telemetry available, fault-period comparison not tested | No fault; five-host PASS | `BLOCKED_PRECONDITION`; prior v3 mechanism remains CONDITIONAL |
| NODE | Worker1 CPU contention | NOT_RUN | NOT_RUN | NOT_EVALUATED; Node CPU metrics and two active business Pods available before fault, but generated workload error gate failed | Worker2 image edge stable; same-node Shipping business span association is topology inference | No fault; four Nodes Ready and five-host PASS | `BLOCKED_PRECONDITION`; prior v3 mechanism remains CONDITIONAL |
| NETWORK_PATH | W3→W2 10% loss candidate | NOT_RUN | NOT_RUN | NOT_EVALUATED; path/flow proved and no-fault probes complete, but there is no loss experiment | Image stable; product-detail p95 1.2514 once exceeds 1.25 | No qdisc was injected; Worker3/2 remained default `mq` | `BLOCKED_PRECONDITION`; prior v3 mechanism remains CONDITIONAL |

Machine-readable statuses and source hashes are in `results/qualification-summary.json`: `PHASE1_NO_READY_MECHANISM`, `faults_injected=0`, `formal_cases_created=0`, `rca_predictions_run=0`, `registration_status=NOT_FROZEN_NO_FAULT`. The planned two-seed registration was **not** created or frozen, so these NOT_RUN entries must not be described as failed fault injections.

## Minimum work before another qualification batch

1. Diagnose the repeated Frontend-proxy→Frontend connection termination during real product requests; show a new full clean window with no generated-workload HTTP failures. Do not declare this recurrent 503 normal noise without a separate, pre-registered justification.
2. For Recommendation Service/Pod mechanisms, establish ≥30 **ordinary business Server Trace IDs per replica per Before minute** under one fixed workload revision, with same-UID telemetry and no overload. Preserve v1/v2 failures; a new revision needs its own fresh no-fault qualification and two fault seeds.
3. For NetworkPath, preregister two independently proven nontraversing controls whose fixed ordinary-business requests, verified edge spans and client p95 all meet the proposed limits throughout a full clean window. Do not relax 1.25 after seeing 1.2514. The full 120/60/120 probe schedule and W3→W2 flow proof already passed, but must be refreshed for any new Pod placement.
4. For Node, keep the per-mechanism During Ready requirement; the independent capture code now supports the real `k8s_node_condition_ready` series, but that new field was **not** retrospectively inserted into v2. Verify it live before a new Node fault. Keep Shipping singleton-Pod inference distinct from direct UID Span identity.
5. Still-unverified Stage5-v3 mechanisms require their own two-seed bounded rehearsal: Service CPU saturation needs real demand on all affected replicas; Ad Pod quota needs pre-fault CPU demand and actual CFS throttling; frontend service-local latency/error needs direct service evidence; Node memory/I/O need independent resource/recovery checks (including the old disk adapter timeout audit); W3→W1 loss, W2→W3 bandwidth and any delay/new path need fresh Pod UID, Calico route, packet and independent control proof after topology changes. Payment Pod-level comparisons remain blocked until both replicas receive ordinary business traffic.

**Freeze decision:** Stage5-v3 is not ready to freeze even a subset of these four Phase 1 mechanisms. The completed tests validate the *no-fault capture and probe pipeline* in this environment; they do not establish root-cause observability, fault injection validity or recovery under fault. Stop here for human review before any additional mechanism, fault or formal Case.
