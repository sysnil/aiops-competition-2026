# Four-layer mechanism engineering readiness

This is a **no-fault engineering audit**, not mechanism qualification or
admission. The current topology snapshot contains four Ready Nodes and 39
Pods. The only current business NetworkPath considered is Worker2→Worker3.

| Layer / intended mechanism | Observed engineering evidence | Status and remaining gate |
| --- | --- | --- |
| SERVICE / Recommendation service-wide stall | Two Ready Recommendation UIDs on Worker3; both have ordinary business Server Spans. The first full Clean's Before last five minutes met ≥30 per replica per minute. | `SERVICE_CONDITIONAL`: independent full-window control qualification and two bounded direct-root/recovery rehearsals remain. |
| POD / single Recommendation Pod memory pressure | Both UID/container/cgroup mappings verified, bounded headroom 261,568,102 and 261,682,995 bytes. The 30-minute Collector window had one sibling at 24/min in minute 5 and below 30 again in minute 29; the later Clean Before coverage passed. | `POD_CONDITIONAL_WORKLOAD_COVERAGE`: identity/resource engineering works, but repeated full-window traffic coverage and two fault/recovery seeds remain. This status is independent of NetworkPath readiness. |
| NODE / Worker1 CPU contention | Four active business Pods; CPU PSI `some avg60` was 0.25% at read-only audit. Node remained Ready throughout Clean. | `NODE_PENDING_SAFETY_CONTRACT`: the proposed 300-second maximum, 360-second independent rollback, Ready/lease/pressure and CPU/PSI/memory abort thresholds are DRAFT, unapproved and untested under fault. No stress was run. |
| NETWORK_PATH / Checkout W2→Cart W3 | Current UID, EndpointSlice, Calico route and dual-ended business packets were proven under the registered workload; full 300/300 no-fault probes passed. | `NETWORK_CONTROL_BLOCKED`: Image direct Span UID missing; Payment W1 has one 1.291× p95 minute and shared Checkout source independence is unverified. No netem was run. |

Current formal qualification permission is **false for all layers**. The
status of one layer is not used to downgrade or qualify another. No fault
mechanism has completed the two independent bounded qualification seeds
required to recommend READY.
