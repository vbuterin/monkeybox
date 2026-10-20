# Extraction provenance

First-party source extracted from `vbuterin/nixos-example`, commit
`937f215` (Complete mbox desktop, clipboard, camera and CLI workflows; preserve
visual exit status). It includes prior work consolidated at `4b79cac`.
No original Git history, host dotfiles, screenshots, credentials, account state,
or broad host configuration were imported.

Original source locations retained for review/test compatibility:

- modules/nixos/mbox.nix: now a generic lib.evalModules module, despite its
  historical directory name. Host NixOS integration is in the separate default.nix.
- programs/mbox/: wrapper, desktop, clipboard, audio, camera and Nix helpers.
- packages/microsandbox/: pinned source build, firmware and focused PR171 backport.
- patches/: scoped host Waypipe registry filter.

Extraction changes: explicit empty host-policy defaults, no sbox/Spaces coupling,
no Noctalia packaging, standalone builder/flake/lock, host utility PATH supplied
from Nix, optional desktop preset and NixOS adapter. Runtime executable names and
state namespaces are intentionally preserved for compatibility. There is no
implicit migration from existing host settings or private guest profiles.

Historical tests/reports remain available in the original repository under
`tests/evidence/mbox-*` and `docs/mbox-*`. Their old store paths and machine names
are evidence of those runs, not requirements or fresh standalone validation.
See tests/README.md for extraction-specific checks.

Backend sources:

- microsandbox 0.7.3 commit f9f40e1a341922c0f66da36f10914d2aad0ca8be.
- libkrun PR #171 commit d51d1c450ee53a494e046da7f7c30c3e79616404,
  backported to locked devices 0.1.39 with zero fuzz and checked vendor updates.
- https://github.com/superradcompany/libkrun/pull/171
- https://github.com/superradcompany/libkrun/issues/172 (separate lifecycle scope).

No upstream PR/issue was modified as part of extraction.
