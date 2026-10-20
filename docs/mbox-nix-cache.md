# Opt-in Nix output reuse in mbox

```sh
mbox PROJECT -C /nix/store/HASH-package -- nix build .#target
mbox PROJECT --nix-cache ./result -- COMMAND
# Repeat -C for additional existing outputs. -v still means a volume mount.
```

**Current prerequisite:** `programs.mbox.shareNixStore = true` (the default).
The guest's Nix executable and ordinary tools still run from the read-only host
store; independence here means a separate writable store/database and no host
daemon access, not removal of all host-store sharing. A self-contained tool
image would be needed to lift this prerequisite.

`-C` / `--nix-cache PATH` accepts an existing top-level store output (or a symlink
to one), not a flake installable, derivation or arbitrary directory. The host
exports its runtime dependency closure to a private temporary file binary cache
using `nix copy`, without requesting builds or missing-path substitution. The
snapshot is mounted read-only at `/run/mbox-nix-cache`. No host daemon, database,
profile or `/nix/var` is shared. Default launches do none of this.

The guest imports that snapshot into a private single-user **chroot Nix store**
at `$HOME/.local/share/mbox-nix/root`, selected with `NIX_REMOTE`. It persists in
the per-project home (unless `--ephemeral`). Nix's Linux user/mount namespaces
provide the logical `/nix/store` for builds; ordinary guest tools still run from
the read-only host store. Use `nix run`/`nix shell` to execute newly built software:
the printed logical output path is not necessarily present in the ordinary
shell's `/nix/store`. Physical output files reside under `$NIX_REMOTE/nix/store`.

Only the explicitly host-selected snapshot import skips signature checking;
other caches keep normal signature checks. The invocation sets its own
`NIX_CONFIG` with sandboxing enabled and an empty build-users-group for the
single-user store. It does not copy host Nix configuration, cache credentials,
agent credentials or signing keys. Hooks/custom images/env and writable mounts
are still trusted configuration, not an adversarial host-user boundary.

## Limits and costs

- This copies data, not a zero-copy overlay. Each launch exports/compresses the
  selected closure afresh; already imported guest paths need not be recopied.
  Temporary export data is removed on ordinary exit, including failures (SIGKILL
  can leave temporary files). Large closures such as Torch consume substantial
  time/disk. No automatic whole-store export.
- A runtime closure is **not** the derivation's full build-time dependency set.
  Export additional inputs or allow guest fetching from configured public caches.
  Blocked networking works only when all required inputs are available.
- Guest builds consume allocated VM CPU/RAM and disk backing the project home;
  there is no new disk quota. No guest request triggers a host build.
  The tested host-shared mounts synthesize exactly **4 GiB available**
  (`f_bavail=1048576`, 4096-byte blocks) and **zero total/free inodes** rather than
  reporting host capacity. The private Nix store lives on the shared home, so
  Nix free-space/GC preconditions based on these statistics are not reliable:
  a full backing filesystem can surface as write failures rather than an early
  capacity warning. This is not evidence that every build/GC command performs
  such a check. Monitor actual backing space on the host. Separately, measured
  statvfs latency is specific to the read-only `/nix/store` share (12–30 seconds
  in retained probes); home/project/root calls were sub-millisecond.
  See [backend limitations and historical evidence](mbox-desktop.md).
  To reclaim unrooted guest outputs during a `-C` session, run
  `nix store gc --store "$NIX_REMOTE"` (the **chroot root**, not its `nix/store`
  subdirectory). This can also remove imported inputs; export/import them again
  when needed. Keep GC roots/profiles for outputs you want retained.
- No nested-KVM/GPU test capability is implied. No host deployment or result
  import happens automatically.
- Explicit grants/mounts remain capabilities: mounting the host state directory
  or `/tmp` broadly can defeat snapshot privacy. The default snapshot is outside
  shared project/home and has mode 0700 on the host.

## Validation

`tests/mbox-nix-cache.py WRAPPER BUSYBOX_STORE_PATH` boots a real blocked-network
VM, imports an existing BusyBox closure, builds a new derivation with sandboxing,
checks the result is absent from the host store, verifies read-only cache/no
host daemon socket, and checks persistence plus no-cache defaults on the next
boot. This passed using the evaluated minimal wrapper; it is not a full NixOS
or Torch build in mbox or a production mount-set test.
