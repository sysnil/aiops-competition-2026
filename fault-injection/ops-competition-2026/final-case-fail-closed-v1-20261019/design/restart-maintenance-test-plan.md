# DRAFT: Master/Worker restart lifecycle qualification

Status: NOT AUTHORIZED. No production host reboot is part of the current turn.

## Why isolated verification is incomplete

The current hosts have no prepared, independently bootable systemd root filesystem or
approved disposable VM. `runc` exists, but a process namespace alone does not reproduce
host PID 1, transient systemd timers, cgroup ownership, SSH startup, or network recovery.
`Persistent=false`, a transient unit, and boot-ID checks therefore remain code-level
protections, not a host restart result. The harmless Marker tests already verify that a
changed Master or Worker boot ID is rejected at trigger time.

## Approval request for a maintenance window

1. Approve one host at a time, first a disposable clone with the same systemd/OpenSSH
   versions; use production Master or Worker2 only if clone fidelity is verified and
   the committee separately accepts the interruption. Do not run any business fault.
2. Preflight five-host health, disk headroom, Calico BGP, Collector/OpenSearch, out-of-band
   console access, and zero active experiment. Stop on any failed preflight.
3. Register a unique Marker-only Attempt with its signed-source manifest and a future
   Worker2 systemd timer. Verify both controller and Guard are active, and record the
   exact timer unit, UTC deadline, Master/Worker boot IDs and source SHA.
4. Reboot the selected host while the Marker timer is pending. After it returns, wait
   beyond the original trigger time and require: no Marker, no pending timer/service,
   a denied or revoked authorization record, and healthy business/telemetry. Repeat with
   a previously revoked Attempt; it must remain non-executable.
5. For a Worker2 reboot, confirm new boot ID, exact target isolation, timer disposition,
   Master Guard response, and that no old signed response is accepted. For a Master
   reboot, confirm the Worker can connect again but Master boot-ID mismatch and absent
   controller/Guard cause denial.
6. Restore any test-only configuration, recheck all five hosts and every competition
   Timer/Service, and archive immutable before/after logs and SHA-256 receipts.

Abort/rollback: the out-of-band administrator may stop only the registered Marker unit,
revoke that Attempt, and restore the SSH forced-command backup if auth provisioning is
itself implicated. An independent cleanup operator must verify no real fault process,
memory holder, qdisc, or timer remains. The 5 GB disk floor and historical Case data
must remain untouched.

Production remains blocked until this test and a fresh 1800-second no-fault run pass on
the final reviewed code version, followed by separate committee production approval.
