"""Making archives and taking them apart.

Python's own `zipfile` and `tarfile` do zip, tar, gzip, bzip2, xz and — since
3.14 — zstd, which is every format anybody hands you except `7z` and `rar`.
Using them rather than shelling out means progress per file, cancellation
between files, and identical behaviour on Windows. The two it cannot read are
handed to `7z` or `bsdtar` if the machine has either, and reported as
unsupported if it has neither.

Two things matter more than the format list.

*Extraction cannot be allowed to write outside the folder it was aimed at.*
An archive member called `../../.bashrc` is a real attack and an easy one;
`tarfile`'s `data` filter refuses those, and the zip path is checked by hand
because `zipfile` has no equivalent.

*Where the files land should be what the person expected.* An archive holding
one top-level folder extracts in place — that folder is the archive's own
idea of where its contents go. An archive holding forty loose files gets a
folder made for it, named after the archive, instead of forty files appearing
in the middle of whatever was already there.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
import zipfile

from . import ops, winenv

COMPRESS = "compress"
EXTRACT = "extract"

# key -> (label, suffix, what writes it)
FORMATS: dict[str, tuple[str, str]] = {
    "zip": ("Zip", ".zip"),
    "tar.gz": ("Tar, gzip", ".tar.gz"),
    "tar.xz": ("Tar, xz", ".tar.xz"),
    "tar.bz2": ("Tar, bzip2", ".tar.bz2"),
    "tar.zst": ("Tar, zstd", ".tar.zst"),
    "tar": ("Tar, no compression", ".tar"),
    "7z": ("7-zip", ".7z"),
}

# How tarfile spells each one's write mode.
_TAR_MODES = {
    "tar": "w",
    "tar.gz": "w:gz",
    "tar.bz2": "w:bz2",
    "tar.xz": "w:xz",
    "tar.zst": "w:zst",
}

# Extensions we can take apart, mapped to how.
_READERS = {
    "zip": "zip", "jar": "zip", "whl": "zip", "xpi": "zip", "apk": "zip",
    "epub": "zip", "odt": "zip", "ods": "zip", "odp": "zip", "docx": "zip",
    "xlsx": "zip", "pptx": "zip", "crx": "zip",
    "tar": "tar", "tgz": "tar", "tbz": "tar", "tbz2": "tar", "txz": "tar",
    "tzst": "tar",
    "tar.gz": "tar", "tar.bz2": "tar", "tar.xz": "tar", "tar.zst": "tar",
    "tar.lzma": "tar", "tar.z": "tar", "pkg.tar.zst": "tar", "pkg.tar.xz": "tar",
    "gz": "tool", "bz2": "tool", "xz": "tool", "zst": "tool", "lz4": "tool",
    "7z": "tool", "rar": "tool", "cab": "tool", "arj": "tool", "lha": "tool",
    "iso": "tool", "deb": "tool", "rpm": "tool", "cpio": "tool", "lzh": "tool",
}


def available_formats() -> list[tuple[str, str, str]]:
    """The formats this machine can write, as (key, label, suffix)."""
    out = []
    for key, (label, suffix) in FORMATS.items():
        if key == "7z":
            if not (winenv.tool("7z") or winenv.tool("7za") or winenv.tool("7zz")):
                continue
        elif key == "tar.zst" and "w:zst" not in _writable_tar_modes():
            if not winenv.tool("bsdtar"):
                continue
        out.append((key, label, suffix))
    return out


def _writable_tar_modes() -> set[str]:
    """Which tar modes this Python actually has the compressors for.

    zstd is stdlib from 3.14; before that, and on a Python built without
    lzma or bz2, asking for the mode raises rather than returns.
    """
    modes = {"w"}
    for module, mode in (("gzip", "w:gz"), ("bz2", "w:bz2"), ("lzma", "w:xz"),
                         ("compression.zstd", "w:zst")):
        try:
            __import__(module)
        except ImportError:
            continue
        modes.add(mode)
    return modes


def detect(path: str) -> str:
    """Which reader to use for this file: "zip", "tar", "tool" or ""."""
    from . import filetypes

    ext = filetypes.extension(os.path.basename(path))
    kind = _READERS.get(ext, "")
    if kind:
        return kind
    # The name is not always the truth, and `file` is not always installed,
    # so fall back to asking the libraries themselves.
    try:
        if zipfile.is_zipfile(path):
            return "zip"
        if tarfile.is_tarfile(path):
            return "tar"
    except OSError:
        return ""
    return ""


def is_archive(path: str) -> bool:
    return bool(detect(path))


class Member:
    """One thing inside an archive."""

    def __init__(self, name: str, size: int = 0, is_dir: bool = False,
                 mtime: float = 0.0):
        self.name = name
        self.size = size
        self.is_dir = is_dir
        self.mtime = mtime


def list_contents(path: str, limit: int = 20000) -> tuple[list[Member], str]:
    """What is in an archive, without extracting it. Returns (members, error)."""
    kind = detect(path)
    try:
        if kind == "zip":
            with zipfile.ZipFile(path) as archive:
                members = []
                for info in archive.infolist()[:limit]:
                    members.append(Member(info.filename, info.file_size,
                                          info.is_dir(),
                                          _zip_time(info)))
                return members, ""
        if kind == "tar":
            with tarfile.open(path) as archive:
                members = []
                for info in archive:
                    members.append(Member(info.name, info.size, info.isdir(),
                                          info.mtime))
                    if len(members) >= limit:
                        break
                return members, ""
        if kind == "tool":
            return _list_with_tool(path)
    except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
        return [], _why(exc)
    return [], "Not an archive this can read"


def _zip_time(info) -> float:
    import time

    try:
        return time.mktime(tuple(info.date_time) + (0, 0, -1))
    except (ValueError, OverflowError, TypeError):
        return 0.0


def _list_with_tool(path: str) -> tuple[list[Member], str]:
    tool = _extract_tool()
    if not tool:
        return [], "No tool on this machine can read that"
    if os.path.basename(tool).startswith("7z"):
        args = [tool, "l", "-ba", "-slt", path]
    else:
        args = [tool, "-tf", path]
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=30,
                              creationflags=winenv.NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], str(exc)
    if done.returncode != 0:
        return [], (done.stderr or "").strip().splitlines()[-1:] and \
            (done.stderr.strip().splitlines()[-1]) or "Could not read it"

    members = []
    if os.path.basename(tool).startswith("7z"):
        name, size, folder = "", 0, False
        for line in done.stdout.splitlines():
            if line.startswith("Path = "):
                if name:
                    members.append(Member(name, size, folder))
                name, size, folder = line[7:], 0, False
            elif line.startswith("Size = "):
                size = int(line[7:] or 0) if line[7:].strip().isdigit() else 0
            elif line.startswith("Attributes = "):
                folder = "D" in line[13:].split()[0] if line[13:].strip() else False
        if name:
            members.append(Member(name, size, folder))
    else:
        for line in done.stdout.splitlines():
            if line.strip():
                members.append(Member(line.rstrip("\n"), 0, line.endswith("/")))
    return members, ""


def _extract_tool() -> str:
    for name in ("7z", "7zz", "7za", "bsdtar"):
        found = winenv.tool(name)
        if found:
            return found
    return ""


def single_root(members: list[Member]) -> str:
    """The one folder every member is inside, or "" if there isn't one.

    This is what decides whether extracting makes a folder or not.
    """
    roots = set()
    for member in members:
        name = member.name.replace("\\", "/").lstrip("/")
        if not name:
            continue
        first = name.split("/", 1)[0]
        if first in ("", ".", ".."):
            return ""
        roots.add(first)
        if len(roots) > 1:
            return ""
    if len(roots) != 1:
        return ""
    only = roots.pop()
    # One member that is a file, not a folder, is not a root.
    if len(members) == 1 and not members[0].is_dir:
        return ""
    return only


def safe_target(destination: str, name: str) -> str:
    """Where a member called `name` may be written, or "" if nowhere.

    The check that stops `../../.bashrc`. Windows separators are normalised
    first, because an archive made on Windows uses them and `..\\..\\x` is
    the same attack.
    """
    cleaned = name.replace("\\", "/")
    parts = [part for part in cleaned.split("/")
             if part not in ("", ".")]
    if any(part == ".." for part in parts):
        return ""
    if not parts:
        return ""
    if os.path.isabs(cleaned) or (len(cleaned) > 1 and cleaned[1] == ":"):
        return ""
    target = os.path.join(destination, *parts)
    if not ops.is_inside(target, destination):
        return ""
    return target


class ArchiveJob(ops.Job):
    """Compressing or extracting, with the progress and cancelling of any job.

    Subclassed from `ops.Job` so that one progress dialog serves every long
    operation in the app: the window does not need to know whether the bar it
    is showing is a copy or a zip.
    """

    def __init__(self, kind: str, sources, destination: str, *,
                 fmt: str = "zip", archive_name: str = "",
                 into_subfolder: bool | None = None, **kwargs):
        super().__init__(kind, sources, destination, **kwargs)
        self.fmt = fmt
        self.archive_name = archive_name
        self.into_subfolder = into_subfolder
        self.archive_path = ""

    def _run(self) -> None:
        if self.kind == COMPRESS:
            self._compress()
        else:
            self._extract()

    # -- making one -------------------------------------------------------

    def _compress(self) -> None:
        plan = ops.measure(self.sources, False, self.stop)
        self.progress.files_total = plan.files + plan.dirs
        self.progress.bytes_total = plan.bytes
        self.progress.phase = "working"

        name = self.archive_name or self._default_name()
        suffix = FORMATS.get(self.fmt, ("", ""))[1]
        if suffix and not name.lower().endswith(suffix):
            name += suffix
        name = ops.unique_name(self.destination, name)
        self.archive_path = os.path.join(self.destination, name)

        # Collected first so that a failure to write member three does not
        # leave a half-made archive looking finished.
        items = list(self._members())
        try:
            if self.fmt == "zip":
                self._write_zip(items)
            elif self.fmt in _TAR_MODES:
                self._write_tar(items)
            elif self.fmt == "7z":
                self._write_with_tool()
            else:
                raise OSError(f"Cannot write {self.fmt} archives here")
        except ops.Cancelled:
            self._discard()
            raise
        except (OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
            self._discard()
            self.progress.errors.append(_why(exc))
            return
        self.result.created.append(self.archive_path)

    def _default_name(self) -> str:
        if len(self.sources) == 1:
            stem = os.path.basename(self.sources[0].rstrip(os.sep))
            base, _ext = ops.split_name(stem)
            return base or stem
        return os.path.basename(self.destination.rstrip(os.sep)) or "archive"

    def _members(self):
        """(path on disk, name inside the archive) for everything going in."""
        for source in self.sources:
            source = source.rstrip(os.sep)
            base = os.path.basename(source)
            if os.path.isdir(source) and not os.path.islink(source):
                yield source, base
                for directory, dirs, files in os.walk(source):
                    dirs.sort()
                    for name in sorted(files) + sorted(dirs):
                        full = os.path.join(directory, name)
                        inside = os.path.join(base, os.path.relpath(full, source))
                        yield full, inside.replace(os.sep, "/")
            else:
                yield source, base

    def _write_zip(self, items) -> None:
        # Deflate, not store: an archive made by a file manager is meant to
        # be smaller than what went in.
        with zipfile.ZipFile(self.archive_path, "w", zipfile.ZIP_DEFLATED,
                             allowZip64=True) as archive:
            for path, inside in items:
                self._tick()
                self._note(path)
                try:
                    if os.path.isdir(path) and not os.path.islink(path):
                        archive.write(path, inside + "/")
                    else:
                        archive.write(path, inside)
                        self.progress.bytes_done += ops._size_of(path)
                except (OSError, ValueError) as exc:
                    self._fail(path, exc if isinstance(exc, OSError)
                               else OSError(str(exc)))
                self.progress.files_done += 1
                self._report()

    def _write_tar(self, items) -> None:
        mode = _TAR_MODES[self.fmt]
        if mode not in _writable_tar_modes():
            self._write_with_tool()
            return
        with tarfile.open(self.archive_path, mode) as archive:
            for path, inside in items:
                self._tick()
                self._note(path)
                try:
                    archive.add(path, arcname=inside, recursive=False)
                    if not os.path.isdir(path):
                        self.progress.bytes_done += ops._size_of(path)
                except (OSError, tarfile.TarError) as exc:
                    self._fail(path, exc if isinstance(exc, OSError)
                               else OSError(str(exc)))
                self.progress.files_done += 1
                self._report()

    def _write_with_tool(self) -> None:
        """Hand it to 7z or bsdtar, for the formats Python cannot write."""
        tool = _extract_tool()
        if not tool:
            raise OSError("No tool on this machine can write that")
        self._note(self.archive_path)
        if os.path.basename(tool).startswith("7z"):
            args = [tool, "a", "-y", self.archive_path] + self.sources
        else:
            compress = {"tar.zst": "--zstd", "tar.xz": "--xz",
                        "tar.gz": "--gzip", "tar.bz2": "--bzip2"}.get(self.fmt, "")
            args = [tool, "-c", "-f", self.archive_path]
            if compress:
                args.append(compress)
            args += ["-C", os.path.dirname(self.sources[0]) or "."]
            args += [os.path.basename(p.rstrip(os.sep)) for p in self.sources]
        done = subprocess.run(args, capture_output=True, text=True,
                              creationflags=winenv.NO_WINDOW)
        if done.returncode != 0:
            raise OSError((done.stderr or done.stdout or "").strip()[:200]
                          or "The archiver failed")
        self.progress.files_done = self.progress.files_total

    def _discard(self) -> None:
        try:
            if self.archive_path and os.path.exists(self.archive_path):
                os.remove(self.archive_path)
        except OSError:
            pass

    # -- taking one apart -------------------------------------------------

    def _extract(self) -> None:
        archive_path = self.sources[0]
        members, error = list_contents(archive_path)
        if error and not members:
            self.progress.errors.append(error)
            return

        self.progress.files_total = len(members) or 1
        self.progress.bytes_total = sum(m.size for m in members)
        self.progress.phase = "working"

        destination = self.destination
        wants_folder = self.into_subfolder
        if wants_folder is None:
            wants_folder = not single_root(members)
        if wants_folder:
            stem, _ext = ops.split_name(os.path.basename(archive_path))
            folder = ops.unique_name(destination, stem or "extracted")
            destination = os.path.join(destination, folder)
            os.makedirs(destination, exist_ok=True)
            self.result.created.append(destination)
        else:
            root = single_root(members)
            if root and os.path.lexists(os.path.join(destination, root)):
                action, _target = self._resolve(
                    os.path.join(archive_path, root),
                    os.path.join(destination, root))
                if action == ops.SKIP:
                    self.result.skipped.append(archive_path)
                    return
                if action == ops.REPLACE:
                    self._remove_tree(os.path.join(destination, root))
            if root:
                self.result.created.append(os.path.join(destination, root))

        kind = detect(archive_path)
        try:
            if kind == "zip":
                self._extract_zip(archive_path, destination)
            elif kind == "tar":
                self._extract_tar(archive_path, destination)
            else:
                self._extract_with_tool(archive_path, destination)
        except ops.Cancelled:
            raise
        except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
            self.progress.errors.append(_why(exc))

    def _extract_zip(self, path: str, destination: str) -> None:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                self._tick()
                self._note(info.filename)
                target = safe_target(destination, info.filename)
                if not target:
                    self.progress.errors.append(
                        f"{info.filename}: refused — it points outside the folder")
                    continue
                try:
                    if info.is_dir():
                        os.makedirs(target, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with archive.open(info) as reader, open(target, "wb") as writer:
                            while True:
                                self._tick()
                                block = reader.read(ops.CHUNK)
                                if not block:
                                    break
                                writer.write(block)
                                self.progress.bytes_done += len(block)
                                self._report()
                        # Zip keeps the Unix mode in the top half of
                        # external_attr, and an executable that comes out
                        # unexecutable is a bug report.
                        mode = info.external_attr >> 16
                        if mode & 0o111:
                            try:
                                os.chmod(target, mode & 0o777)
                            except OSError:
                                pass
                except OSError as exc:
                    self._fail(info.filename, exc)
                self.progress.files_done += 1

    def _extract_tar(self, path: str, destination: str) -> None:
        with tarfile.open(path) as archive:
            for info in archive:
                self._tick()
                self._note(info.name)
                if not safe_target(destination, info.name):
                    self.progress.errors.append(
                        f"{info.name}: refused — it points outside the folder")
                    continue
                try:
                    # The `data` filter is the one that refuses absolute
                    # paths, parent traversal, devices and setuid bits.
                    archive.extract(info, destination, filter="data")
                    self.progress.bytes_done += info.size
                except (OSError, tarfile.TarError) as exc:
                    message = _why(exc)
                    self.progress.errors.append(f"{info.name}: {message}")
                self.progress.files_done += 1
                self._report()

    def _extract_with_tool(self, path: str, destination: str) -> None:
        tool = _extract_tool()
        if not tool:
            raise OSError("No tool on this machine can read that")
        self._note(path)
        if os.path.basename(tool).startswith("7z"):
            args = [tool, "x", "-y", f"-o{destination}", path]
        else:
            args = [tool, "-x", "-f", path, "-C", destination]
        done = subprocess.run(args, capture_output=True, text=True,
                              creationflags=winenv.NO_WINDOW)
        if done.returncode != 0:
            raise OSError((done.stderr or done.stdout or "").strip()[:200]
                          or "The archiver failed")
        self.progress.files_done = self.progress.files_total


def _why(exc: Exception) -> str:
    if isinstance(exc, OSError):
        return ops._explain(exc)
    text = str(exc) or type(exc).__name__
    return text[:200]


def compress(sources, destination: str, fmt: str = "zip", **kwargs) -> ArchiveJob:
    return ArchiveJob(COMPRESS, sources, destination, fmt=fmt, **kwargs)


def extract(archive_path: str, destination: str, **kwargs) -> ArchiveJob:
    return ArchiveJob(EXTRACT, [archive_path], destination, **kwargs)
