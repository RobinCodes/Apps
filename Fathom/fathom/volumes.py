"""The things there are to scan, before you have picked one.

WizTree opens on a list of drives with a bar each, and that is the right
first screen: the question "what is taking up space" almost always starts at
a whole filesystem. Unix makes that list a little less obvious than Windows
does — there is one tree, not several letters — so this reads the mount table
and throws away everything that is not storage.
"""

from __future__ import annotations

import os
import shutil

from . import winenv

# Filesystems that hold bytes on a device. Everything else in /proc/mounts is
# the kernel talking to itself: tmpfs is RAM, proc and sysfs are made up on
# read, overlay and squashfs belong to the container or snap that mounted them
# and are counted where they came from.
REAL_FILESYSTEMS = frozenset({
    "ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "jfs", "reiserfs", "zfs",
    "bcachefs", "nilfs2", "ntfs", "ntfs3", "fuseblk", "vfat", "exfat", "msdos",
    "hfsplus", "apfs", "udf", "iso9660", "ufs",
})

# Mounted over the network: real storage, but a walk of one is a different
# animal — slow, and not the user's disk. Listed, and marked.
NETWORK_FILESYSTEMS = frozenset({
    "nfs", "nfs4", "cifs", "smb3", "smbfs", "sshfs", "afs", "ceph", "glusterfs",
})


class Volume:
    __slots__ = ("path", "label", "device", "fstype", "total", "free", "network")

    def __init__(self, path, label, device="", fstype="", network=False):
        self.path = path
        self.label = label
        self.device = device
        self.fstype = fstype
        self.network = network
        self.total = 0
        self.free = 0
        try:
            usage = shutil.disk_usage(path)
            self.total, self.free = usage.total, usage.free
        except OSError:
            pass

    @property
    def used(self) -> int:
        return max(0, self.total - self.free)

    @property
    def fraction(self) -> float:
        return (self.used / self.total) if self.total else 0.0


def _linux_mounts() -> list[Volume]:
    found: list[Volume] = []
    seen_devices: set[str] = set()
    try:
        with open("/proc/self/mounts", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return found

    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        device, target, fstype = parts[0], parts[1], parts[2]
        network = fstype in NETWORK_FILESYSTEMS
        if fstype not in REAL_FILESYSTEMS and not network:
            continue
        # /proc/mounts escapes the characters that would otherwise break the
        # column layout it is written in.
        target = (target.replace("\\040", " ").replace("\\011", "\t")
                        .replace("\\012", "\n").replace("\\134", "\\"))
        if not os.path.isdir(target):
            continue
        # btrfs subvolumes and bind mounts appear once per mount but are one
        # filesystem with one free-space figure; the first, shallowest one is
        # the one worth offering.
        if device in seen_devices:
            continue
        seen_devices.add(device)
        label = target if target != "/" else "Root filesystem"
        found.append(Volume(target, label, device, fstype, network))

    found.sort(key=lambda vol: (vol.network, vol.path.count(os.sep), vol.path))
    return found


def _windows_drives() -> list[Volume]:
    import ctypes
    import string

    found: list[Volume] = []
    try:
        kernel32 = ctypes.windll.kernel32          # type: ignore[attr-defined]
        mask = kernel32.GetLogicalDrives()
    except (AttributeError, OSError):
        return found

    for bit, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << bit):
            continue
        root = f"{letter}:\\"
        # 3 is fixed, 4 is a network drive, 2 is removable. 5 is a CD and 1 is
        # a letter with nothing behind it; neither is worth an entry.
        try:
            kind = kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        except OSError:
            kind = 3
        if kind not in (2, 3, 4):
            continue
        name = ctypes.create_unicode_buffer(261)
        try:
            kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root), name, 261, None, None, None, None, 0)
        except OSError:
            pass
        label = f"{letter}:  {name.value}".rstrip() if name.value else f"{letter}:"
        found.append(Volume(root, label, device=root, fstype="",
                            network=(kind == 4)))
    return found


def volumes() -> list[Volume]:
    """Every filesystem worth offering, plus home, biggest tree first."""
    found = _windows_drives() if winenv.WINDOWS else _linux_mounts()

    home = os.path.expanduser("~")
    if os.path.isdir(home) and not any(vol.path == home for vol in found):
        # Home is almost always a directory on a filesystem already listed
        # rather than a mount of its own, and it is almost always the answer,
        # so it gets an entry regardless.
        entry = Volume(home, "Home folder")
        entry.device = ""
        found.insert(0, entry)
    return found
