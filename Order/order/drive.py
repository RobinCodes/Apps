"""Reorganising a drive without ever losing anything on it.

A reorganisation is a *plan* — a list of moves, each with its reason — that is
read, then *applied*. Applying writes a journal line before each move and
another after it, so even a crash halfway leaves an exact record of what
happened, and `undo` walks the journal backwards. Nothing is ever deleted:
a duplicate is moved aside, not removed, and a move whose destination is
taken is skipped and reported rather than merged or overwritten.

    order drive apply E:\\.order\\plan-2026-10-27.jsonl
    order drive undo  E:\\.order\\journal-2026-10-27-1530.jsonl

Each journal also gets a PowerShell and a shell undo script beside it, so a
move can be reversed on a machine without this tool.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Move:
    source: str          # relative to the drive root, forward slashes
    target: str
    why: str = ""


def read_plan(path: Path) -> list[Move]:
    moves = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rec = json.loads(line)
        if not rec.get("from") or not rec.get("to"):
            raise ValueError(f"{path}:{n}: a move needs from and to")
        moves.append(Move(rec["from"].replace("\\", "/"), rec["to"].replace("\\", "/"),
                          rec.get("why", "")))
    return moves


def write_plan(path: Path, moves: list[Move], header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as out:
        for line in header.splitlines():
            out.write(f"# {line}\n")
        for m in moves:
            out.write(json.dumps({"from": m.source, "to": m.target, "why": m.why},
                                 ensure_ascii=False) + "\n")


def check(root: Path, moves: list[Move]) -> list[tuple[Move, str]]:
    """What would go wrong, before anything moves: (move, problem) pairs."""
    problems = []
    targets = set()
    for m in moves:
        src, dst = root / m.source, root / m.target
        if not src.exists():
            problems.append((m, "source does not exist"))
        if dst.exists():
            problems.append((m, "destination already exists"))
        if m.target in targets:
            problems.append((m, "two moves share a destination"))
        targets.add(m.target)
        try:
            dst.relative_to(src)
            problems.append((m, "destination is inside the source"))
        except ValueError:
            pass
    return problems


def apply(root: Path, moves: list[Move], journal_dir: Path | None = None,
          report=print) -> Path:
    """Carry out the moves, journalled. Returns the journal's path."""
    journal_dir = journal_dir or root / ".order"
    journal_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    journal = journal_dir / f"journal-{stamp}.jsonl"
    done = 0
    with journal.open("a", encoding="utf-8", newline="\n") as log:
        def note(**rec):
            log.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"), **rec},
                                 ensure_ascii=False) + "\n")
            log.flush()
            os.fsync(log.fileno())

        for m in moves:
            src, dst = root / m.source, root / m.target
            if not src.exists():
                note(event="skip", **{"from": m.source, "to": m.target}, why="source gone")
                report(f"  skip  {m.source}  (no longer there)")
                continue
            if dst.exists():
                note(event="skip", **{"from": m.source, "to": m.target}, why="destination taken")
                report(f"  skip  {m.source}  (destination {m.target} is taken)")
                continue
            note(event="begin", **{"from": m.source, "to": m.target}, why=m.why)
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(src, dst)              # same volume: instant, atomic
            except OSError:
                shutil.move(str(src), str(dst))  # across volumes: copy, then remove
            note(event="done", **{"from": m.source, "to": m.target})
            done += 1
            report(f"  moved {m.source}  →  {m.target}")
    _write_undo_scripts(root, journal)
    report(f"{done} moved; journal {journal}")
    return journal


def journal_moves(journal: Path) -> list[Move]:
    """The moves a journal records as completed, in the order they happened."""
    finished = []
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event") == "done":
            finished.append(Move(rec["from"], rec["to"]))
    return finished


def undo(root: Path, journal: Path, report=print) -> int:
    """Reverse every completed move in a journal, last first. Returns how many."""
    undone = 0
    for m in reversed(journal_moves(journal)):
        src, dst = root / m.target, root / m.source
        if not src.exists():
            report(f"  skip  {m.target}  (no longer there)")
            continue
        if dst.exists():
            report(f"  skip  {m.target}  ({m.source} is taken again)")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)
        undone += 1
        report(f"  back  {m.target}  →  {m.source}")
    with journal.open("a", encoding="utf-8", newline="\n") as log:
        log.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"),
                              "event": "undone", "count": undone}) + "\n")
    return undone


def _sh_quoted(text: str) -> str:
    """Safe inside a double-quoted sh string."""
    for ch in ("\\", '"', "$", "`"):
        text = text.replace(ch, "\\" + ch)
    return text


def _write_undo_scripts(root: Path, journal: Path) -> None:
    moves = list(reversed(journal_moves(journal)))
    ps = ["# Undo " + journal.name + ": puts every moved item back, last first.",
          "$root = Split-Path -Parent $PSScriptRoot",
          "$ErrorActionPreference = 'Continue'"]
    sh = ["#!/bin/sh", "# Undo " + journal.name + ": puts every moved item back, last first.",
          'root="$(cd "$(dirname "$0")/.." && pwd)"']
    for m in moves:
        src = m.target.replace("'", "''")
        dst = m.source.replace("'", "''")
        ps.append(f"if (-not (Test-Path -LiteralPath (Join-Path $root '{dst}'))) {{ "
                  f"New-Item -ItemType Directory -Force -Path (Split-Path -Parent (Join-Path $root '{dst}')) | Out-Null; "
                  f"Move-Item -LiteralPath (Join-Path $root '{src}') -Destination (Join-Path $root '{dst}') }}")
        q_src, q_dst = _sh_quoted(m.target), _sh_quoted(m.source)
        sh.append(f"[ -e \"$root/{q_dst}\" ] || {{ mkdir -p \"$(dirname \"$root/{q_dst}\")\"; "
                  f"mv \"$root/{q_src}\" \"$root/{q_dst}\"; }}")
    base = journal.with_suffix("")
    (base.parent / (base.name.replace("journal-", "undo-") + ".ps1")).write_text(
        "\r\n".join(ps) + "\r\n", encoding="utf-8-sig")
    (base.parent / (base.name.replace("journal-", "undo-") + ".sh")).write_text(
        "\n".join(sh) + "\n", encoding="utf-8")
