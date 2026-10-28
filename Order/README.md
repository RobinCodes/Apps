# Order

Every machine laid out the same, and kept that way.

```bash
order sync        # clone what is missing, fast-forward what is behind, sync the brain
order doctor      # where this machine differs from the manifest
order where       # this machine's name, and the paths the Order uses
```

The layout itself — what goes where, what things are called, how a thing
travels from the inbox to its place — is the second brain's `SYSTEM.md`. This
folder is the part of it that runs: it reads the **manifest**
(`~/SecondBrain/config/order.yaml`, private, because it is a list of someone's
repositories) and makes the machine it runs on match it.

## A new machine

One command. It is safe to run again, and running it again is how a
half-finished machine gets finished.

```powershell
# Windows
irm https://raw.githubusercontent.com/RobinCodes/Apps/main/Order/bootstrap.ps1 | iex
```

```sh
# Arch
curl -fsSL https://raw.githubusercontent.com/RobinCodes/Apps/main/Order/bootstrap.sh | sh
```

On Windows that is Git, MSYS2 with UCRT64's GTK4 stack, a name for the
machine, the apps, the brain (Git Credential Manager asks to sign in to GitHub
once), every repository in the manifest, the Start Menu shortcuts and file
types, the sync timer, `anam` and `order` on PATH, and Claude Code. On Arch the
same, through pacman, `~/.local/bin` and `.desktop` entries, and a systemd user
timer. Neither needs an administrator beyond pacman's one `sudo`.

## What sync does, and what it refuses to do

Three things, and only three:

| | |
|---|---|
| **clone** | a repository the manifest lists and the machine lacks |
| **fast-forward** | a repository that is clean and only behind |
| **commit, rebase, push** | the brain, and nothing else (`sync: push` in the manifest), through `anam sync` |

Everything else is reported and left alone: local edits, unpushed commits, a
diverged branch, a folder in a repository's place that is not one, a
repository with a different remote. A sync that destroyed work to make two
machines agree would be worse than two machines that disagree, so the Order
never forces, never stashes, never resets. `order doctor` lists what needs a
person; `order adopt` handles the one common case — a folder that predates the
manifest — by making it the manifest's repository *without touching a single
file in it*: its history becomes the remote's, and its differences are left
as uncommitted changes to look at.

The brain goes first in every sync, because it carries the manifest, and a
newer manifest may list more. Once a day (`refresh.every_hours`) the sync
also starts `anam refresh` in the background.

## The timer

```bash
order timer install     # every ten minutes
order timer status
order timer remove
```

A scheduled task (`Order\Sync`) on Windows, run with `pythonw.exe` so no
console flashes up; a systemd user timer (`order-sync.timer`, two minutes
after boot and every ten minutes after) on Linux. Both run as the user.

## Reorganising a drive

The archive drive (SYSTEM.md §5) is reorganised by *plans*: a JSON-lines list
of moves, each with its reason, read before anything happens.

```bash
order drive check  E:\.order\plan-2026-10-27.jsonl     # what would go wrong
order drive apply  E:\.order\plan-2026-10-27.jsonl     # do it, journalled
order drive undo   E:\.order\journal-2026-10-27-153012.jsonl
```

Applying writes a journal line before each move and another after it, so a
crash halfway still leaves an exact record. A move whose destination is taken
is skipped and reported, never merged or overwritten. Nothing is deleted. Each
journal gets an `undo-….ps1` and `undo-….sh` beside it, which put everything
back without this tool.

## The manifest

```yaml
repositories:
  - path: SecondBrain
    url: https://github.com/RobinCodes/SecondBrain.git
    sync: push                 # the brain: commit, pull --rebase, push
  - path: Projects/Programming/Apps
    url: https://github.com/RobinCodes/Apps.git
  - path: Projects/Programming/Scripts/Arch
    url: https://github.com/RobinCodes/Arch.git
    machines: [arch]           # only there
  - path: Projects/Overleaf/Digitalization
    url: https://git.overleaf.com/…
    optional: true             # needs a sign-in the Order does not have
places:
  - SecondBrain/inbox
refresh:
  every_hours: 24
```

A machine is named by `ORDER_MACHINE` (the bootstrap asks once), or else by
its hostname, lower-cased. The name is what review logs, nodes and sync
commits record, so it should not change once chosen.

## Tests

```bash
python3 Order/tests/test_order.py
```

Bare repositories in a scratch folder stand in for GitHub: cloning, fast-
forwarding, refusing to update over local work, adopting a folder without
losing a file, the doctor's findings, and a drive plan checked, applied and
undone. No network, and nothing outside the scratch folder is touched.
