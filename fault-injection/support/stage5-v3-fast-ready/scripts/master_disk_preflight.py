#!/usr/bin/env python3
"""Fail closed unless the approved 300 GiB VMware disk is blank and unused."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess


DISK = Path("/dev/sdb")
BY_PATH = Path("/dev/disk/by-path/pci-0000:00:10.0-scsi-0:0:1:0")
EXPECTED_BYTES = 322122547200
OUTPUT = Path("/root/stage5-v3-fast-ready/snapshots/master-new-disk-preflight.json")
MOUNT = Path("/data/stage5-v3")


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=20)


def checked(*args):
    result = run(*args)
    if result.returncode:
        raise RuntimeError("Command failed: " + " ".join(args) + " " + result.stderr[-300:])
    return result.stdout


def main():
    if OUTPUT.exists():
        raise ValueError("Refusing preflight overwrite")
    by_path_target = os.path.realpath(BY_PATH)
    if by_path_target != str(DISK):
        raise ValueError("Approved by-path identity no longer points to /dev/sdb")
    disk = json.loads(checked("lsblk", "-J", "-b", "-o",
        "NAME,PATH,SIZE,TYPE,FSTYPE,UUID,MOUNTPOINTS,PKNAME", str(DISK)))["blockdevices"]
    root_disk = json.loads(checked("lsblk", "-J", "-b", "-o",
        "NAME,PATH,SIZE,TYPE,FSTYPE,UUID,MOUNTPOINTS,PKNAME", "/dev/sda"))[
            "blockdevices"]
    if (len(disk) != 1 or disk[0]["name"] != "sdb" or disk[0]["path"] != str(DISK)
            or disk[0]["size"] != EXPECTED_BYTES or disk[0]["type"] != "disk"
            or disk[0].get("children") or disk[0].get("fstype") or disk[0].get("uuid")
            or any(disk[0].get("mountpoints") or []) or len(root_disk) != 1 or
            root_disk[0]["name"] != "sda" or root_disk[0]["size"] !=
            107374182400):
        raise ValueError("Disk size, root identity, partitions, FS, or mount changed")
    probe = run("blkid", "-p", str(DISK))
    if probe.returncode != 2 or probe.stdout.strip():
        raise ValueError("Disk has a recognizable filesystem or signature")
    signatures = checked("wipefs", "-n", str(DISK))
    if signatures.strip():
        raise ValueError("Disk has a wipefs signature")
    partition_table = run("sfdisk", "-d", str(DISK))
    if partition_table.returncode != 1 or "does not contain a recognized" not in (
            partition_table.stderr):
        raise ValueError("Disk already has a partition table or unexpected error")
    mounted = run("findmnt", "-S", str(DISK))
    if mounted.returncode != 1 or mounted.stdout.strip():
        raise ValueError("Disk is mounted or findmnt failed unexpectedly")
    if list(Path("/sys/block/sdb/holders").iterdir()):
        raise ValueError("Disk has a block-device holder")
    pv = checked("pvs", "--reportformat", "json", "-o", "pv_name,vg_name")
    physical_volumes = json.loads(pv)["report"][0]["pv"]
    if any(row["pv_name"] == str(DISK) or row["pv_name"].startswith(str(DISK))
           for row in physical_volumes):
        raise ValueError("Disk is already an LVM physical volume")
    swaps = Path("/proc/swaps").read_text()
    if str(DISK) in swaps:
        raise ValueError("Disk is swap")
    fstab = Path("/etc/fstab")
    fstab_text = fstab.read_text()
    if str(DISK) in fstab_text or str(MOUNT) in fstab_text or MOUNT.exists():
        raise ValueError("Mountpoint or fstab entry already exists")
    device_busy = run("lsof", "-nP", str(DISK))
    if device_busy.returncode not in (0, 1) or device_busy.stdout.splitlines()[1:]:
        raise ValueError("Disk has an open process handle")
    result = {"status": "APPROVED_BLANK_DISK_PREFLIGHT_PASS",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "approved_disk": str(DISK), "stable_by_path": str(BY_PATH),
        "by_path_resolves_to": by_path_target, "disk_bytes": disk[0]["size"],
        "root_disk": "/dev/sda", "root_disk_bytes": root_disk[0]["size"],
        "filesystem": None, "partitions": [], "mountpoints": [],
        "wipefs_signatures": [], "lvm_pvs": physical_volumes,
        "fstab_sha256_before": hashlib.sha256(fstab_text.encode()).hexdigest(),
        "mountpoint_preexisted": False, "read_only_audit": True}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "disk": result["approved_disk"],
                      "bytes": result["disk_bytes"], "root": result["root_disk"]}))


if __name__ == "__main__":
    main()
