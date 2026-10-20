# Virtual keyboard protocol

`virtual-keyboard-unstable-v1.xml` is vendored unchanged from wtype 0.4's
`protocol/virtual-keyboard-unstable-v1.xml` (the selected nixpkgs source at the
time of import, `/nix/store/hsjfmbi35qhnm2hzhrhcp8ip41gkahfr-source`).
SHA256: `7ad7870003ecd592cae47dc19d277a609b7f18fd7b7be012623cf3225a7294f5`.
The XML contains its upstream copyright and MIT permission notice.

The fixed clipboard helper now builds from this repository copy, independent of
wtype's package/source layout. It uses this protocol only within the guest-local
compositor. The outer Waypipe allowlist still excludes virtual keyboard access.
