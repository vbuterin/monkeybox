# Console event before activation aborts VM startup

A reported `mbox-online pi` startup failed with SIGABRT before the agent relay
became ready. The host core report identified `panic_bounds_check` in
`msb_krun_devices::virtio::console::event_handler::Console::process`.
Disassembly of the reported installed `microsandbox-0.7.3-vsock-pr171` executable
mapped its return address `msb + 0x1a35670` to the bounds check for queue index 2.
The two older Quickshell core reports were separate crashes from another boot.

## Cause and fix

In locked backend 0.1.39, `Console::new()` initializes `queue_events` empty.
`activate()` populates it; `reset()` clears it. The console subscriber registers
activation, SIGWINCH, and control notifications before activation. Its event
handler indexed control queue entries 2 and 3 *before* checking `is_activated()`.
An event while the queues are absent therefore panics instead of following the
existing inactive-device warning path. The original report does not establish
which event triggered this instance; terminal resize is a possibility, not a
confirmed trigger.

`patches/microsandbox-console-inactive-events.patch` moves queue/source lookups
inside the existing activation guard. It leaves active-device dispatch and
inactive-event handling otherwise unchanged. This is not a panic catcher,
retry loop, or relaxed bounds check. It does not change guest permissions or
sandbox state. Pending inactive events retain the existing warning behavior;
the patch does not introduce a new event-draining policy.

The source package applies this patch to its vendored backend beside the
existing PR #171 backport, updates the modified file's Cargo checksum, and runs
the console tests in `checkPhase`.

## Regression evidence

Tests added to the actual backend handler cover:

- Each initially registered event source before activation, with empty queues.
- Events after a real activation/reset cycle, including a stale queue event.
- An active SIGWINCH notification as a positive control: its eventfd is consumed.

In an isolated temporary workspace using the pinned package source, lockfile,
and vendored dependencies, both new tests fail against the original handler
with `index out of bounds: the len is 0 but the index is 2`. Both pass with the
patch. The device crate was cleaned between variants so Cargo could not reuse
an unchanged registry-dependency build.

With both the console and PR #171 patches applied, these actual Rust test
filters passed using `cargo test --frozen -p msb_krun_devices --lib FILTER`:

| Filter | Passed |
| --- | ---: |
| `virtio::console::` | 15 |
| `virtio::vsock::packet::tests` | 8 |
| `virtio::vsock::custom_stream::tests` | 4 |

The console patch applied with `--fuzz=0`; the existing PR #171 patch also
applied with `--fuzz=0` (its existing line offsets were accepted). The Python
package-policy guard passed. These were offline Cargo tests using the debug test
profile, not a Nix build, the package's CI profile, or a full VM startup test.
No host configuration was evaluated or activated for this validation.

## Deployment

Rebuild/deploy the updated Monkeybox package through the usual host workflow.
The existing installed `msb` executable remains affected until replaced. Newly
launched VMs use the deployed fix; running VMs do not hot-load it. No deletion
of project state, sandbox images, or application profiles is required.
