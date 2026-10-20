# Exit status and test diagnostics

Direct private `msb run --no-tty` preserves guest exit codes 0, 42, 127 and
143. Before the fix, visual `mbox -v` / `-vc` returned zero for failing commands.
The loss is Waypipe server's application-child boundary, not msb's no-tty path.

The guest desktop helper now wraps its command with an invocation-local status
file. Nonzero command status takes precedence; successful commands retain a
nonzero bridge status. Missing/malformed status fails closed with exit 1. This
file is guest-local, not a trusted security signal from an untrusted application.
Ctrl+C's bounded host cancellation remains separate and returns 130.

`tests/mbox-exit-status.py FIXTURE_WRAPPER` compares actual private VM runs with
direct msb invocations and verifies missing values against the built wrapper.
Current built wrappers already reject zero-path `-f`; an additional local guard
makes the variadic handler self-contained. Options after a command still belong
to that command, and shell nullglob can remove arguments before mbox sees them.

Desktop integration tests now reject non-fixture wrappers before boot, using an
immutable hook marker. Noctalia preview tests reject wrong/unpatched executables
before creating the private compositor. Both print actionable usage errors.

Historical evidence: original repository tests/evidence/mbox-nits/ at the extraction commit.
Standalone validation is listed in tests/README.md. No physical
camera or live clipboard input used. Runtime fixture uses a private HOME/Sway/VM.
