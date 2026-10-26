#!/usr/bin/env python3
"""Mount only the approved new ext4 partition and verify UUID fstab remount."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess


ROOT = Path("/root/stage5-v3-fast-ready")
PARTITION = Path("/dev/sdb1")
BY_PATH = Path("/dev/disk/by-path/pci-0000:00:10.0-scsi-0:0:1:0-part1")
UUID = "a35051a2-3d09-4b83-bbc0-3bb055e8a4f3"
MOUNT = Path("/data/stage5-v3")
FSTAB = Path("/etc/fstab")
ENTRY = "UUID=" + UUID + " " + str(MOUNT) + (
    " ext4 defaults,nodev,nosuid,noexec,nofail,x-systemd.device-timeout=10s 0 2\n")
BACKUP = ROOT / "snapshots/fstab-before-stage5v3-disk-20261013"
OUTPUT = ROOT / "results/master-data-disk-mount.json"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=35)
    if result.returncode:
        raise RuntimeError("Command failed: " + " ".join(args) + " " + result.stderr[-300:])
    return result.stdout.strip()


def verify_identity():
    if os.path.realpath(BY_PATH) != str(PARTITION):
        raise ValueError("Partition by-path changed")
    info = json.loads(checked("lsblk", "-J", "-b", "-o",
        "NAME,PATH,SIZE,TYPE,FSTYPE,UUID,MOUNTPOINTS,PKNAME", str(PARTITION)))[
            "blockdevices"]
    if (len(info) != 1 or info[0]["name"] != "sdb1" or
            info[0]["pkname"] != "sdb" or info[0]["type"] != "part" or
            info[0]["fstype"] != "ext4" or info[0]["uuid"] != UUID or
            info[0]["size"] != 322120450048 or
            checked("findmnt", "-n", "-o", "SOURCE", "/") !=
            "/dev/mapper/ubuntu--vg-ubuntu--lv"):
        raise ValueError("Approved partition or root device changed")


def verify_mounted():
    source = checked("findmnt", "-n", "-o", "SOURCE", "-T", str(MOUNT))
    fs = checked("findmnt", "-n", "-o", "FSTYPE", "-T", str(MOUNT))
    uuid = checked("findmnt", "-n", "-o", "UUID", "-T", str(MOUNT))
    if (source != str(PARTITION) or fs != "ext4" or uuid != UUID or
            os.stat(MOUNT).st_dev == os.stat("/").st_dev):
        raise RuntimeError("Mount does not resolve to the approved data filesystem")


def main():
    if OUTPUT.exists() or BACKUP.exists():
        raise ValueError("Refusing mount evidence overwrite")
    verify_identity()
    if MOUNT.exists() or "UUID=" + UUID in FSTAB.read_text() or str(MOUNT) in FSTAB.read_text():
        raise ValueError("Mountpoint or fstab entry already exists")
    root_pre = shutil.disk_usage("/").free
    fstab_bytes = FSTAB.read_bytes()
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    BACKUP.write_bytes(fstab_bytes)
    os.chmod(BACKUP, 0o600)
    MOUNT.mkdir(parents=True, mode=0o750)
    if list(MOUNT.iterdir()):
        raise ValueError("New mountpoint is not empty")
    checked("mount", "-U", UUID, str(MOUNT))
    verify_mounted()
    original_stat = FSTAB.stat()
    temporary = Path("/etc/fstab.stage5v3-20261013.tmp")
    if temporary.exists():
        raise ValueError("Temporary fstab path already exists")
    try:
        with temporary.open("xb") as stream:
            stream.write(fstab_bytes.rstrip(b"\n") + b"\n" + ENTRY.encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(original_stat.st_mode))
        os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
        os.replace(temporary, FSTAB)
        checked("findmnt", "--verify", "--tab-file", str(FSTAB))
        checked("systemctl", "daemon-reload")
        checked("umount", str(MOUNT))
        checked("mount", str(MOUNT))
        verify_mounted()
    except Exception:
        if FSTAB.read_bytes() != fstab_bytes:
            FSTAB.write_bytes(fstab_bytes)
            os.chmod(FSTAB, stat.S_IMODE(original_stat.st_mode))
            checked("systemctl", "daemon-reload")
        if os.stat(MOUNT).st_dev == os.stat("/").st_dev:
            checked("mount", "-U", UUID, str(MOUNT))
        raise
    data = shutil.disk_usage(MOUNT)
    result = {"status": "MASTER_STAGE5V3_DATA_MOUNT_VERIFIED",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "partition": str(PARTITION), "partition_by_path": str(BY_PATH),
        "filesystem": "ext4", "uuid": UUID, "mountpoint": str(MOUNT),
        "mount_options": ENTRY.split()[3],
        "fstab_entry": ENTRY.strip(), "fstab_sha256_before": sha(fstab_bytes),
        "fstab_sha256_after": sha(FSTAB.read_bytes()),
        "fstab_backup": str(BACKUP), "fstab_backup_sha256": sha(BACKUP.read_bytes()),
        "fstab_remount_test": "PASS", "data_total_bytes": data.total,
        "data_available_bytes": data.free,
        "root_available_before_bytes": root_pre,
        "root_available_after_bytes": shutil.disk_usage("/").free,
        "distinct_filesystem_from_root": True,
        "existing_kubernetes_or_observability_data_moved": False}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "uuid": UUID,
        "mountpoint": str(MOUNT), "available_bytes": data.free}))


if __name__ == "__main__":
    main()
