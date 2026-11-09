# Apps — notes for agents

Thirteen desktop apps (GTK4/libadwaita in Python, plus Lyndon in C) and **Order**, the tool
that keeps Robin's machines identical. See `README.md` for the list and what is common.

**Every app updates itself from `origin/main`.** A push to `main` reaches all of Robin's
machines within minutes. So: run the full suite and gate on its real exit code (never
through `| tail`), `git pull --rebase`, run it again, then push.

The apps that read and write the second brain (Ariadne, Clio, Hermione, Mind Palace,
Mnemosyne, Notedown, The Eye) follow a contract kept in the private brain repository
(`SYSTEM.md` in `RobinCodes/SecondBrain`, cloned at `~/SecondBrain`); its
`docs/INFRASTRUCTURE.md` is the full technical map of both repositories. If you have
access, read them before changing brain-facing behaviour. The state as of 2026-11-09 and
the jobs ahead are on Robin's archive drive (My Passport): `CLAUDE.md` at its root and
`Handoff/README.md`.

Rules that are easy to break:
- `house/` holds the originals of the shared modules (`theme`, `housestyle`, `updater`
  for every app; `brain`, `cards`, `fsrs`, `claude`, `frontmatter`, `threads`, `timelines`
  for brain apps). Edit them **only there**, then `python house/sync.py`; the suite fails on drift.
- An app joins Mind Palace with `<package>/room.py` — the contract is `Mind Palace/ROOMS.md`;
  copy the shape of `notedown/room.py`.
- Windows: GTK comes from MSYS2 **UCRT64** (`C:\msys64\ucrt64\bin\python.exe`, with
  `C:\msys64\ucrt64\bin` first on PATH); launchers fall back to MINGW64 for old installs.
  Run `run-tests.py` from a normal environment, never through MSYS2's `/usr/bin` tools.
  Every subprocess started on Windows passes `creationflags=0x08000000` (CREATE_NO_WINDOW),
  or a console flashes on screen.
- git run by automation: `GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never`, with a timeout.
- GStreamer reads a backslash in pipeline text as an escape, quoted or not: never write a
  Windows path into `parse_launch` or `gst-launch-1.0`; set `location` as a property, or
  use forward slashes.
- Tests build their own scratch worlds and must not read or write real user state
  (point `ORDER_HOME`, `LOCALAPPDATA`/`XDG_CACHE_HOME` at scratch folders).
- Display tests wait for things with `settle(condition, ms)`, which returns as soon as the
  condition holds: give it seconds, not a tight deadline, or a busy machine fails it.
- Every app has its icon committed in `windows/icons/`; the installer only makes the ones
  missing, and an untracked generated icon would block `git pull` once it is committed.
- Each app's README is written in one voice — precise, explanatory, a little literary — and
  says honestly what is not tested. Keep it so.
- Commits end with a `Co-Authored-By:` line naming the model.
