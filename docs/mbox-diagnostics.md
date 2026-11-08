# Diagnostics mode

`mbox --diagnostics` (or `MBOX_DIAGNOSTICS=1`) turns a run into its own
incident report: one 0600 file per invocation under
`<state root>/logs/diag-<UTC timestamp>-<sandbox>.log`, containing every
layer of the stack in reading order. Nothing is parsed back by the
launcher; the file exists for post-run inspection (by a human or an
agent) and replaces the old habit of re-running a failure three times to
catch three different logs.

## What lands in the file

- **Header** — timestamp, which launcher build ran (`script:` store
  path), msb path, project/state/sandbox identity.
- **Launcher decisions** — resolved profile defaults, the final mount
  grant list, net mode, the TTY relay decision, resolved image/cpus/
  memory, the exact guest command chain, waypipe/audio/camera readiness
  timings, and the msb exit status.
- **Session transcript** — from the header on, everything the launcher
  and the guest print on stdout/stderr is teed into the file (the
  terminal still shows it as usual; `nm`-style backgrounding still
  works — the file is written regardless of where stdout points).
- **Guest preamble** — the guest session script introduces itself:
  kernel, user, memory, full guest environment, mount table, private
  runtime contents, dmesg tail.
- **Input events** — under `-v`, a `wev` recorder starts as a sibling
  of the app inside the waypipe session (pointer/keyboard press and
  release pairs, modifiers). It pulls `nixpkgs#wev` over the guest
  network with a 120 s cap and only notes its absence on failure;
  set `MBOX_DIAG_NO_WEV=1` to skip it. Read the pairs: a press without
  a matching release is the lost-button-release signature.
- **Clipboard** — `mbox-clipboard` register/unregister/export/import
  events with sizes and the session they targeted.
- **Wine** — with a wayland session, `WINEDEBUG` defaults to
  `+loaddll,+process` (loaded DLLs and process spawns — the missing
  WebView2 class of failure). Override with `WINEDEBUG` (as usual) or
  `MBOX_DIAG_WINEDEBUG` (diagnostics-only, e.g. `+server` for deep
  traces).
- **Footer** — after the session ends, the exit trap appends msb's
  system log (`msb logs --source system <sandbox>`) and a closing
  marker; runs that die before a sandbox exists leave the
  "(msb system log unavailable)" line instead.

## Usage notes

- Works with every mode: plain shells, `-v` GUIs, `-e` ephemeral runs,
  `--dry-run` (the log then holds the full resolved plan).
- The file may contain environment values, paths, input transcripts and
  clipboard sizes — never clipboard payloads. It is created 0600 inside
  the private state root; delete old logs at will, nothing reads them.
- Interleaving between the concurrent tees is line-granular; sections
  are marked `===== mbox diag [section]` so a scan for that prefix
  gives the table of contents.
- `mbox --diagnostics` first resolves profiles and re-execs; the flag
  survives the re-exec (it is part of the original argv), and
  `profiles.py` reserves the name so an abbreviation cannot shadow it.

## Test

`python3 tests/mbox-diagnostics.py` dry-runs the real launcher against a
stub msb in a private HOME and asserts single-file creation, 0600 mode,
every section, the guest-env argument, section order, and that no logs
directory appears without the flag.
