# Stage5-v3 autonomous preparation

Independent rehearsal and DRAFT protocol-executor workspace. It does not modify
the frozen Stage5-v1/v2 archives, open Stage5-v2 TEST, run a formal RCA method,
or freeze a Stage5-v3 benchmark protocol.

## Synthetic protocol executor

`scripts/draft_executor.py` is deliberately synthetic-only. It reads the copied
DRAFT allocation, records each transition as a new event manifest, hashes every
input, replays and verifies the archive before GT unseal, and rejects unresolved
candidates, unsafe cleanup, invalid variants, invalid rankings and private
evidence references. `events/*.json` form a SHA-256 hash chain; the final event
hash must be published separately when an archive is used as an audit anchor.

Run offline checks:

```sh
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -v
```

The command-line entry point is `scripts/draft_executor_cli.py`. It requires
an empty synthetic archive initialized with `init`; every later command
checks `.synthetic-only` and refuses any other directory. Use
`design/dataset-allocation-draft.yaml` and
`design/synthetic-method-scope.json` for an offline registration. The
`scripts/run_synthetic_demo.py` harness creates a complete example with both
split barriers and no real GT.

The tests construct only synthetic evidence and GT in temporary directories.
The prototype does not provide production filesystem/mount isolation, real
adapter execution, real model inference, or a real GT-unseal credential. Those
must be reviewed before any formal Pilot.

## Clean candidate C1

`design/load-plan-c1.yaml` fixes 10+5+10 minutes, 1.8 sessions/s and the prior
fixed ordinary-business controls. The proposed W3→W2 nontraversing controls
are image (Worker2 local) and cart (Frontend Worker1/2→Cart Worker2); fresh
Pod UID, Calico route and payload-bearing packet proof is required in each run.
The existing 30/min and 1.25 p95-ratio gates are unchanged. Product Detail
remains in the workload and in error accounting even though it is no longer
one of the two proposed network controls.

Clean runs are separate rehearsal archives in `runs/`. A run with any generated
HTTP/gRPC failure is a failed qualification window, even if a later retry or
another independent health gate is green. No fault can start until two full
same-version Clean PASS windows and mechanism-specific preflight have been
verified.

## Clean candidate C2

C1 was retained as failed evidence. Candidate C2 restores the original
1.5-session/s MEDIUM rate, applies the audited Frontend-proxy upstream
connection repair and pins the Proxy back to its pretrial Worker2 placement.
The exact same Image/Cart controls and 1.25 p95 ratio remain in force.
Because C1's first capture finished at +61.1 seconds and raced the immutable
+60-second trace supplement, C2 starts each first read at +5 seconds instead
of +20; the supplement remains fixed at +60 and may never overwrite the first
snapshot. `scripts/analyze_clean_c2.py` hashes the first snapshot, verifies
the supplement manifest and analyzes its complete span set. This timing change
is a pre-freeze candidate that needs explicit review before v3 protocol freeze.

## Clean candidate C3

C2's two full windows are retained separately. They both met workload
identity and no-error checks but exceeded the predeclared 0.25-core Worker3
load ceiling; one also exceeded the unchanged 1.25 per-minute control p95
ratio. C3 is the final candidate allowed in this preparation batch. It removes
the unused fixed Product Detail stream and sets MEDIUM to 1.4 sessions/s while
keeping real product calls in every state-machine session, Image/Cart controls,
the same 30/min coverage rules, the 1.25 p95 ratio and the 0.25-core ceiling.
The unused preliminary C3 plan/profile are preserved under `design/`.

`design/clean-c3-candidate-manifest.json` on Master pins its input and code
hashes. `run_clean_c3.py` checks that manifest, its exact ID/seed pair and
the repaired Proxy Deployment template before any clean run. This is only a
rehearsal candidate lock. The formal Stage5-v3 protocol remains DRAFT.
