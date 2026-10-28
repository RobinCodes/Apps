"""The manifest: which repositories and places belong where, read from the brain.

It lives in the brain rather than here because it is personal — the list of
someone's repositories — and because the brain is the one thing every machine
already syncs. This module only knows how to read it and what this machine is.
"""

from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("ORDER_HOME") or Path.home())


def brain() -> Path:
    return Path(os.environ.get("ANAM_HOME") or home() / "SecondBrain")


def manifest_path() -> Path:
    return brain() / "config" / "order.yaml"


def machine() -> str:
    """This machine's name in the Order: ORDER_MACHINE, else the hostname, lower-cased."""
    name = os.environ.get("ORDER_MACHINE") or socket.gethostname() or "unknown"
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "unknown"


SYNC_MODES = ("push", "pull", "none")


@dataclass(frozen=True)
class Repo:
    path: str                       # home-relative, forward slashes
    url: str
    sync: str = "pull"
    optional: bool = False
    machines: tuple[str, ...] = ()

    @property
    def where(self) -> Path:
        return home() / self.path

    def here(self, name: str | None = None) -> bool:
        """Whether this repository belongs on this machine."""
        return not self.machines or (name or machine()) in self.machines

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass
class Manifest:
    repositories: list[Repo] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    refresh_hours: float = 24.0
    source: Path | None = None

    def for_this_machine(self) -> list[Repo]:
        name = machine()
        return [r for r in self.repositories if r.here(name)]


class ManifestError(Exception):
    pass


def parse(data: dict, source: Path | None = None) -> Manifest:
    if not isinstance(data, dict):
        raise ManifestError("the manifest is not a mapping")
    repos = []
    seen = set()
    for i, entry in enumerate(data.get("repositories") or []):
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("url"):
            raise ManifestError(f"repository #{i + 1} needs a path and a url")
        path = str(entry["path"]).replace("\\", "/").strip("/")
        if path in seen:
            raise ManifestError(f"{path} is listed twice")
        seen.add(path)
        sync = str(entry.get("sync") or "pull")
        if sync not in SYNC_MODES:
            raise ManifestError(f"{path}: sync must be one of {', '.join(SYNC_MODES)}")
        machines = entry.get("machines") or ()
        if isinstance(machines, str):
            machines = [machines]
        repos.append(Repo(path=path, url=str(entry["url"]), sync=sync,
                          optional=bool(entry.get("optional")),
                          machines=tuple(str(m).lower() for m in machines)))
    places = [str(p).replace("\\", "/").strip("/") for p in data.get("places") or []]
    refresh = (data.get("refresh") or {}).get("every_hours", 24)
    return Manifest(repositories=repos, places=places,
                    refresh_hours=float(refresh), source=source)


def load(path: Path | None = None) -> Manifest:
    path = path or manifest_path()
    try:
        import yaml                                         # noqa: PLC0415
    except ImportError as exc:
        raise ManifestError("PyYAML is needed to read the manifest "
                            "(pacman -S python-yaml, or MSYS2's python-yaml)") from exc
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"no manifest at {path}") from exc
    try:
        return parse(yaml.safe_load(text) or {}, path)
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path} is not valid YAML: {exc}") from exc
