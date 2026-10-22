# Experimental opt-in camera

`-l` / `--camera` enables a camera-only bridge; default device `/dev/video0`.
Use `--camera-device /dev/video2` to select another capture node; this option
alone does not enable capture. `--no-camera` disables it. Example:

```sh
mbox -avicl -- firefox
# or select a different device:
mbox -vicl --camera-device /dev/video2 -- firefox
```

In the **guest Firefox profile**, enable `media.webrtc.camera.allow-pipewire` in
`about:config` (requires browser restart). This is not enabled by default in the
reviewed Firefox; no host profile or browser permission preference is modified
by mbox. The website still requests camera permission normally. The available
camera is named **Mbox camera**. Initial format is fixed **320×240 at 25 fps**.
This is low-resolution experimental support, not a high-quality-call claim.

`-a` grants playback and `-r` grants microphone input independently; `-l` does not grant a microphone.
Bluetooth `-b` is deferred: connected headsets use `-a`, keyboards/mice use normal
focused visual input. The user has now confirmed **Firefox camera access works with their camera**.
Signal/Electron compatibility is still unverified. No physical camera was opened or live video recorded by
the assistant; all runtime tests used generated patterns.

## Permission and lifecycle boundary

A host helper opens only the selected V4L2 character device (major 81), validates
its FD and passes that FD to ffmpeg via `/proc/self/fd`. The guest receives only
fixed 307200-byte BGRA frames over vsock 5004. It cannot send capture commands,
select another device, or access host PipeWire/D-Bus. Reverse data or disconnect
ends capture. Buffering is capped at two frames; stalled/slow receivers fail
visibly. Capture begins when the guest bridge connects, **before website camera
permission**, and lasts for the invocation. Do not expect closing a browser tab
to revoke the VM's grant; exit mbox to stop the host capture process.

A private guest bus, camera-only PipeWire graph, policy-only WirePlumber and
camera portal expose this stream locally. All applications in the opted-in VM
can access it; the portal is compatibility plumbing, not per-app isolation.
The local bus does not provide file chooser, screen sharing, or other portals.
Device format negotiation on real webcams, unplug/replug, prolonged load and
application-specific video calls still need validation. Failure must not be
worked around by granting the host bus or entire device tree.

## Orphan capture cleanup (post-resume report)

A user reported camera-busy launch failures after suspend/resume. Host diagnosis
found a ~22.5-hour-old FFmpeg with PPID 1 holding the selected camera; the exact
capture command/UID/start time were checked. SIGTERM did not stop it; targeted
SIGKILL released the device and the next Signal launch succeeded. This proves
the immediate blockage, not the original suspend/driver failure sequence.

Private `tests/mbox-camera-lifecycle.py` reproduced two orphan paths on the old
helper with a fake capture process that ignores SIGTERM:

- EOF enters cleanup, then another TERM raises SystemExit from inside the
  grace-period wait, skipping the SIGKILL fallback and child reaping.
- SIGKILL of the bridge cannot run Python finally; the capture child had no
  parent-death protection and survived as an orphan.

The helper now ignores TERM/INT/HUP during cleanup, blocks those signals around
Popen until the child handle is recorded, and arms Linux PR_SET_PDEATHSIG=SIGKILL
before capture exec with a parent-identity recheck. This preexec hook is confined
to the single-threaded Linux helper; do not reuse it in a multithreaded process.
Unexpected bridge death revokes capture even if FFmpeg ignores TERM. A process
stuck in uninterruptible kernel I/O may still take time to exit after SIGKILL.

Regression tests cover repeated TERM during cleanup, bridge SIGKILL, HUP, stalled
capture watchdog and normal TERM; synthetic frame/EOF/reverse-data tests remain
passing. They open no physical camera, inject no desktop input and do not suspend
the host. Real suspend/resume capture recovery is not proven. The monotonic
watchdog does not count Linux suspend time itself; a post-resume stall/disconnect
is a possible trigger, not an observed driver diagnosis. A camera failure still
terminates this camera-enabled guest path; this patch prevents orphan capture,
not seamless video-call recovery or automatic capture restart.

## Backend constraints

The selected microsandbox 0.7.3 CLI has no camera/USB device passthrough API.
A virtiofs mount of a host character device cannot forward its driver/ioctl state
across kernels. A reviewed Nix libkrunfw source configuration
(`/nix/store/jp8hpbh34527xx7x00ak3id84yfm6fa7-source/config-libkrunfw_x86_64`)
has `CONFIG_MEDIA_SUPPORT`, `CONFIG_MODULES`, `CONFIG_USB_SUPPORT`, and
`CONFIG_CUSE` disabled. This is **not a verified config extraction from the
bundled release firmware**; `/proc/config.gz` was unavailable in the private
runtime probe. The running bundled kernel reported 6.12.109. Therefore do not
claim loading a v4l2loopback module or CUSE camera works on this artifact.

## Candidate paths

1. **Guest-local PipeWire camera provider and camera portal**, fed by a narrow
   host capture process over a dedicated vsock stream. No host PipeWire or D-Bus
   socket is shared. Host selects the capture device; cap dimensions/fps/frame
   size and validate framing before allocation, bound backpressure, stop capture
   on invocation exit. Guest applications may control only guest-local nodes.
   Firefox's camera portal can obtain a guest-local PipeWire remote; Chromium/
   Signal compatibility must be demonstrated rather than assumed.
2. **Guest firmware with built-in V4L2 + loopback capture driver**, fed by the
   same narrow frame bridge. This offers a conventional `/dev/videoN` across
   applications, but requires a new reproducible firmware build, maintenance,
   and regression validation beyond the currently pinned release firmware.
3. PipeWire's V4L2 compatibility library intercepts `/dev/videoN` opens/ioctls and
   mmap in userspace. Potential fallback for applications without portal camera
   support, **not yet validated** with Firefox/Signal process sandboxes. Global
   LD_PRELOAD or a claim of transparent universal compatibility is inappropriate.

The portal's `OpenPipeWireRemote` returns a Unix FD. Raw socat/vsock forwarding
cannot carry cross-kernel FD/shared-memory semantics. Forwarding the whole host
session bus or PipeWire socket is neither a working shortcut nor an acceptable
permission boundary. An MJPEG URL alone is not a browser-selectable webcam.

## Sources actually inspected

Fetched 2026-10-20 (mutable upstream references, research only, not build inputs):
- https://raw.githubusercontent.com/flatpak/xdg-desktop-portal/main/data/org.freedesktop.portal.Camera.xml
  — full camera interface, permission response, `OpenPipeWireRemote` FD contract.
- https://raw.githubusercontent.com/PipeWire/pipewire/master/pipewire-v4l2/src/pipewire-v4l2.c
  — inspected `/dev/video` interception, Video/Source discovery, ioctl/mmap paths.
- Local selected microsandbox CLI/runtime source and the above firmware config.

## Validation completed and remaining

Disposable Firefox 156.0.1 successfully captured synthetic 320×240/25fps camera
pixels in a private host-kernel bwrap control, then a real mbox VM. Three bridge
runs verified red top/green bottom frames generated outside the VM travelled
through vsock, guest PipeWire, the camera portal and getUserMedia. The final
production guest helper also passes this test. Host helper tests use mocked
V4L2 metadata and a synthetic capture process: exact frames, reverse-data/EOF
revocation and non-V4L device refusal pass. These are **not physical capture tests**.

Earlier failures are retained: tool-output resolution, absent D-Bus configuration,
browser startup timeouts, a SPA node without node-level camera format enumeration,
and a portal signal delivery bug. Firefox startup trace found another slow store
fstatfs probe on **Firefox's own bundled font directory**. The disposable test
profile disables bundled fonts; this is not a production browser preference
change or proof all browser-startup delays are repaired. A subsequent normal
visual Firefox regression was reproduced and addressed with a narrow immutable
font-directory mount; see [font limitations](mbox-desktop.md).

User subsequently confirmed Firefox camera capture works. Next: Chromium/Electron/Signal
compatibility and sustained quality/lifecycle tests. Test-only browser permission
bypass is confined to disposable synthetic profiles, never installed by mbox.
