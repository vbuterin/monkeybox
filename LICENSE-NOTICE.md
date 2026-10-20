# License selection pending

The source repository supplied no root license. This extraction does not invent
or change a license grant for first-party code. Before inviting public reuse,
the owner should select an appropriate license and add LICENSE (MIT or Apache-2.0
are conventional options). Until then, do not describe the project as licensed
open source or assume that publication grants redistribution rights.

Third-party notices are retained:

- programs/mbox/protocol/virtual-keyboard-unstable-v1.xml carries its MIT license
  and copyright; protocol/README.md records its upstream origin and checksum.
- patches/mbox-waypipe-globals.patch modifies Waypipe; its upstream license
  continues to apply to the built derivative.
- packages/microsandbox fetches microsandbox, libkrun patches, firmware and Rust
  dependencies under their respective upstream licenses. No binaries or vendor
  trees are copied into this Git repository; Nix builds/fetches verified inputs.

A root license would not supersede any of those dependency licenses.
