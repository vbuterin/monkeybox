{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.mbox;

  # Scoped to mbox; the host side enforces the registry allowlist even against
  # a modified guest proxy. The ordinary host waypipe package is unchanged.
  mboxWaypipe = pkgs.waypipe.overrideAttrs (old: {
    patches = (old.patches or []) ++ [ ../../patches/mbox-waypipe-globals.patch ];
  });

  # msb needs libcap-ng and nothing else outside libc, so the prebuilt release binary
  # is a small patchelf away from working; rustls is linked statically and the guest
  # agent (agentd) is embedded at build time by crates/filesystem/build.rs, which is
  # why there is no runtime fetch from GitHub here.
  msbPackage = pkgs.stdenvNoCC.mkDerivation (finalAttrs: {
    pname = "microsandbox";
    version = "0.7.3";

    src = pkgs.fetchurl {
      url = "https://github.com/superradcompany/microsandbox/releases/download/v${finalAttrs.version}/microsandbox-linux-x86_64.tar.gz";
      hash = "sha256-LV2n3hhyRsgE3OKhoj8GYmtvfLiS2aDHxcYgeEukEuo=";
    };

    nativeBuildInputs = [ pkgs.autoPatchelfHook ];
    # The manylinux baseline the binary was built against expects libgcc_s, which a
    # NixOS cc wrapper does not put on the default search path.
    buildInputs = [ pkgs.libcap_ng pkgs.gcc.cc.libgcc ];

    # The release bundle holds msb and the firmware at the archive root, with no
    # top-level directory, so the default unpacker extracts them into the build
    # directory itself and reports "produced no directories". Extract by hand into a
    # directory of our own and point sourceRoot at it.
    unpackPhase = ''
      runHook preUnpack
      mkdir -p bundle
      tar -xf $src -C bundle
      runHook postUnpack
    '';
    sourceRoot = "bundle";

    dontConfigure = true;
    dontBuild = true;

    installPhase = ''
      runHook preInstall

      install -Dm755 msb $out/bin/msb

      # The bundle carries its own guest firmware, newer than nixpkgs' libkrunfw, and
      # msb looks for it next to the binary or in $ORIGIN/../lib. Both layouts are
      # provided, plus an unsuffixed name for the wrapper to point MSB_LIBKRUNFW_PATH
      # at without knowing the firmware version.
      for f in libkrunfw.so.*; do
        install -Dm644 "$f" "$out/lib/$f"
        ln -sf "$f" "$out/lib/$(basename "$f" | cut -d. -f1-2)"
      done
      install -Dm644 libkrunfw.so.* "$out/lib/libkrunfw.so"

      mkdir -p $out/bin
      ln -sf $out/bin/msb $out/bin/microsandbox

      runHook postInstall
    '';

    # The bundle is built against a manylinux baseline, so a library we cannot see is
    # a build-time error rather than a runtime one.
    strictDeps = true;
  });

  # The reference the built-in rootfs is imported under; programs.mbox.image defaults
  # to it, and the wrapper compares against this constant to decide whether a registry
  # is involved at all.
  baseImageRef = "mbox-base:latest";

  # Copy real font bytes into the image: aggregate-store statfs can stall.
  # Keep packages separately named, so identically named font files cannot collide.
  guestFontPackages = lib.unique ([ pkgs.dejavu_fonts pkgs.dejavu_fonts.minimal ] ++ cfg.fontPackages);
  guestFontPath = p: "/usr/share/mbox-fonts/${builtins.baseNameOf (toString p)}";
  guestFontsBase = pkgs.makeFontsConf {
    fontDirectories = guestFontPackages;
    impureFontDirectories = [];
    # Restore fontconfig's rendering/substitution rules, not just font discovery.
    includes = [ "${pkgs.fontconfig.out}/etc/fonts/conf.d" ] ++ cfg.fontConfigIncludes;
  };
  # Fontconfig probes font directories with statfs even when caches exist.
  # Built-in image carries real font bytes on its local root, not store symlinks.
  # Custom images retain the immutable store fallback through guestFontsBase.
  guestFontsConf = pkgs.runCommand "mbox-fonts-local.conf" {} ''
    substitute ${guestFontsBase} $out ${lib.concatMapStringsSep " " (p:
      "--replace-fail ${lib.escapeShellArg (toString p)} ${lib.escapeShellArg (guestFontPath p)}"
    ) guestFontPackages}
  '';

  # A microVM needs a filesystem to mount as / before anything can run, which is the
  # one job the base image has: nothing inside it is used, since the interpreter and
  # every tool arrive through the read-only /nix/store mount. Building it here instead
  # of pulling alpine means no registry, no network, and nothing to do the first time
  # someone runs mbox. busybox is in it only so /bin/sh and the few utilities some
  # programs go looking for exist; it costs about nothing.
  # A few files the guest needs that no package installs: msb looks the sandbox user up
  # in the image's own /etc/passwd and refuses to boot without it ("failed to resolve
  # guest uid 0"), and git, python and anything calling getpwuid ask the same file. Only
  # a root line is needed, because the guest runs as root and the mounts translate
  # ownership on the way out. copyToRoot supplies the package tree; extraCommands
  # adds the guest-specific account, certificate and font files.
  baseImage = pkgs.runCommand "mbox-base.tar" { nativeBuildInputs = [ pkgs.gzip ]; } ''
    gunzip -c ${pkgs.dockerTools.buildImage {
      name = "mbox-base";
      tag = "latest";
      # Same package trees as the former contents argument. In this pinned
      # dockerTools buildImage both resolve to rootContents, preserving layout.
      copyToRoot = [ pkgs.busybox pkgs.bashInteractive ];
      config = { Env = [ "PATH=/bin" ]; };
      extraCommands = lib.concatStringsSep "\n" [
        # NSS/p11-kit reads this standard path; SSL_CERT_FILE alone is not enough.
        "mkdir -p ./etc/ssl/certs"
        "ln -s ${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt ./etc/ssl/certs/ca-certificates.crt"
        (lib.concatMapStringsSep "\n" (p: ''
          mkdir -p .${guestFontPath p}
          cp -rL ${p}/share/fonts/. .${guestFontPath p}/
        '') guestFontPackages)
        "mkdir -p ./etc/fonts"
        "ln -s ${guestFontsConf} ./etc/fonts/fonts.conf"
        "printf 'root:x:0:0:root:/root:/bin/sh\\n' > ./etc/passwd"
        "printf 'root:x:0:\\n' > ./etc/group"
        "printf 'passwd: files\\ngroup: files\\n' > ./etc/nsswitch.conf"
        # No link is created for /bin/bash: buildImage already links each entry of
        # `copyToRoot` into the image root, which is what puts bash and sh there (measured —
        # attempting the link by hand reports "File exists", then "Permission denied",
        # because the root is read-only by the time extraCommands run). guestShell names
        # that file, so if this list ever stops carrying bashInteractive, the guest loses
        # its shell and the wrapper's pre-flight check is what says so. guestShell names a
        # store path rather than this /bin/bash, because msb has to be able to find the
        # shell on the host too.
      ];
    }} > $out
  '';

  # msb's tar reader also rejects gzip'd archives ("numeric field did not have utf-8
  # text ... when getting cksum"), which is a second reason to avoid buildLayeredImage:
  # buildImage's tar is already plain, so the file above is imported as it is.

  # A baseline userland, on the guest's PATH ahead of the image's /bin. The base image is
  # busybox, and busybox's tools are the size-optimised builds: it prints '?' for characters
  # it cannot represent, so a file named привет-你好.txt lists as ??????-??.txt while echo
  # prints the same bytes correctly. It looks like a locale problem and is not one (measured:
  # /bin/ls -> busybox, LANG=C.UTF-8, echo fine, ls not). Anything a shell is expected to
  # answer with should come from the store like everything else, with busybox behind it as a
  # fallback rather than as the interface.
  #
  # These are linked into one environment with programs.mbox.packages, so a command named in
  # both lists fails the build on the collision; remove it from one of them.
  userlandDefaults = with pkgs; [
    coreutils
    findutils
    gnugrep
    gnused
    gawk
    diffutils
    which
    file
    less
    procps
    fontconfig
    gnutar
    gzip
    bzip2
    xz
  ];

  # One environment for the guest, exactly like the one sbox puts on PATH: a venv can
  # only take its interpreter from a single base, so a second python env on PATH would
  # make the packages of the first one disappear.
  mboxEnv = pkgs.buildEnv {
    name = "mbox-env";
    paths = cfg.userlandPackages ++ cfg.packages;
    pathsToLink = [ "/bin" ];
  };

  # Every library named in a wheel's NEEDED entries has to be findable by directory, so
  # this is the search path handed to the guest's loader.
  libDirsString = lib.concatStringsSep ":" (map (d: "${d}/lib") cfg.guestLibraryPath);

  hookText = cfg.shellHook + "\n" + ''
    # Also supports custom images lacking /etc/fonts; keep cache in private home.
    if [ -d /usr/share/mbox-fonts ]; then
      export FONTCONFIG_FILE=${guestFontsConf}
    else
      export FONTCONFIG_FILE=${guestFontsBase}
    fi

    # libstdc++ and friends, for the C extensions that pip installs into the venv.
    # On the host these are found through /etc/ld.so.cache, which NixOS generates from
    # the system profile; the guest sees neither that file nor /run/current-system,
    # because only /nix/store is mounted. Without this, a wheel that links libstdc++
    # installs happily and then fails at import with "cannot open shared object file"
    # — numpy, pandas and torch all behave this way.
    export LD_LIBRARY_PATH="@@LIBDIRS@@''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

    # The sandbox marks the prompt rather than dictating it: whatever prompt the bashrc
    # configures survives, with this in front of it. Assigned through PROMPT_COMMAND rather
    # than directly, because the bashrc is sourced after this hook by the shell that draws
    # the prompt, so a value set here would be overwritten; and the guard against the prefix
    # already being present is what stops it accumulating once per prompt. Marking rather than
    # replacing also means a venv activation's "(.venv)" cannot displace the sandbox name.
    export MBOX_PS1_PREFIX='\[\e[1;35m\][mbox]\[\e[0m\] '
    mbox_mark_prompt() {
        case "$PS1" in
            "$MBOX_PS1_PREFIX"*) ;;
            *) PS1="$MBOX_PS1_PREFIX$PS1" ;;
        esac
    }
    export -f mbox_mark_prompt
    export PROMPT_COMMAND="mbox_mark_prompt''${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
  '';

  # Aliases cannot travel in the environment, so the interactive shell has to read them from
  # a file. Pointing bash at /etc/bash.bashrc was the first attempt and it silently did not
  # happen — this bash does not read a system rc on its own — so the interactive shell is
  # started with --rcfile naming this file instead, which does not depend on how bash was
  # configured. It ends by sourcing the caller's own ~/.bashrc, so theirs is read last and
  # wins: this file only adds what a sandbox should not have to be told twice.
  bashrcSystem = pkgs.writeText "mbox-bash.bashrc" ''
    for _cmd in ls grep fgrep egrep; do
      case "''$_cmd" in
        ls)   alias ls='ls --color=auto' ;;
        grep) alias grep='grep --color=auto' ;;
        *)    alias "''$_cmd"="''$_cmd --color=auto" ;;
      esac
    done
    unset _cmd

    [ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"
  '';

  # The hook is a derivation of its own, so the token inside it has to be substituted
  # here as well as in the script. Leaving it in produces a hook that exports the literal
  # text "@@LIBDIRS@@", which arrives as a missing library rather than as a broken build.
  hookFile = pkgs.writeText "mbox-shellhook.bash"
    (builtins.replaceStrings [ "@@LIBDIRS@@" ] [ libDirsString ] hookText);

  # Paths that may contain $HOME are left as-is: the wrapper expands them on the host,
  # and the guest home is mounted at the same absolute path, so host and guest agree.
  quotedList = builtins.concatStringsSep " " (map (p: "\"${p}\"") cfg.persist);
  bindEntries = builtins.concatStringsSep " " (
    map (p: "\"${p}:${p}\"") (builtins.attrNames cfg.bind)
  );
  bindReadOnlyEntries = builtins.concatStringsSep " " (
    map (p: "\"${p}:${p}\"") (builtins.attrNames cfg.bindReadOnly)
  );

  loopbackGuest = pkgs.writeShellScript "mbox-host-loopback" (builtins.replaceStrings
    [ "@@PYTHON@@" "@@SOCAT@@" "@@PI_BIN@@" ]
    [ "${pkgs.python3}/bin/python3" "${pkgs.socat}/bin/socat"
      "${mboxEnv}/bin" ]
    (builtins.readFile ../../programs/mbox/host-loopback.sh.in));

  nixGuest = pkgs.writeShellScript "mbox-nix-guest" (builtins.replaceStrings
    [ "@@NIX@@" ] [ "${pkgs.nix}" ]
    (builtins.readFile ../../programs/mbox/nix-guest.sh.in));

  # Waypipe 0.11 spawn_xwls_handler resolves this exact wrapper via PATH, passes
  # DISPLAY as argv[1], then -listenfd descriptors inherited across exec, and
  # sets WAYLAND_DISPLAY. Keep satellite compatible with that contract on upgrades.
  # Never expose the host X socket; enforce SHM/no-TCP after forwarded arguments.
  guestSatellite = pkgs.writeShellScriptBin "xwayland-satellite" ''
    display=$1
    shift
    exec ${pkgs.xwayland-satellite}/bin/xwayland-satellite "$display" "$@" -glamor none -nolisten tcp
  '';

  clipboardKey = pkgs.stdenv.mkDerivation {
    pname = "mbox-clipboard-key";
    version = "1";
    dontUnpack = true;
    nativeBuildInputs = [ pkgs.pkg-config pkgs.wayland-scanner ];
    buildInputs = [ pkgs.wayland pkgs.libxkbcommon ];
    buildPhase = ''
      wayland-scanner client-header ${../../programs/mbox/protocol/virtual-keyboard-unstable-v1.xml} virtual-keyboard.h
      wayland-scanner private-code ${../../programs/mbox/protocol/virtual-keyboard-unstable-v1.xml} virtual-keyboard.c
      $CC -Wall -Wextra -Werror ${../../programs/mbox/clipboard-key.c} virtual-keyboard.c -I. \
        $(pkg-config --cflags --libs wayland-client xkbcommon) -o mbox-clipboard-key
    '';
    doCheck = true;
    checkPhase = ''
      runHook preCheck
      mkdir -m 700 runtime
      export XDG_RUNTIME_DIR="$PWD/runtime" WAYLAND_DISPLAY=missing-display
      unset WAYLAND_SOCKET
      for action in copy paste; do
        status=0
        ./mbox-clipboard-key "$action" || status=$?
        test "$status" = 1
      done
      status=0
      ./mbox-clipboard-key arbitrary-input || status=$?
      test "$status" = 2
      runHook postCheck
    '';
    installPhase = ''mkdir -p $out/bin; cp mbox-clipboard-key $out/bin/'';
    meta = {
      description = "Fixed Copy/Paste key requests for the guest-local mbox compositor";
      platforms = lib.platforms.linux;
      mainProgram = "mbox-clipboard-key";
    };
  };

  clipboardGuest = pkgs.writeShellScript "mbox-clipboard-guest" (builtins.replaceStrings
    [ "@@WL_PASTE@@" "@@WL_COPY@@" "@@KEY@@" ]
    [ "${pkgs.wl-clipboard}/bin/wl-paste" "${pkgs.wl-clipboard}/bin/wl-copy" "${clipboardKey}/bin/mbox-clipboard-key" ]
    (builtins.readFile ../../programs/mbox/clipboard-guest.sh.in));

  # Guest-only: preserve the parent's keymap and consume host IME commits.
  # The host still exposes only the application-side text-input protocol.
  nestedWlroots = pkgs.wlroots_0_20.overrideAttrs (old: {
    patches = (old.patches or []) ++ [ ../../patches/mbox-wlroots-input.patch ];
    postPatch = (old.postPatch or "") + ''
      cp ${../../programs/mbox/nested-input.c} backend/wayland/nested-input.c
      cp ${../../programs/mbox/caret.h} backend/wayland/caret.h
      cp ${../../programs/mbox/sway-caret-state.h} backend/wayland/sway-caret-state.h
      cp ${../../tests/mbox-caret-protocol.c} mbox-caret-protocol-test.c
      substituteInPlace mbox-caret-protocol-test.c \
        --replace-fail '../programs/mbox/sway-caret-state.h' 'backend/wayland/sway-caret-state.h'
      cp ${../../tests/mbox-nested-input.c} mbox-nested-input-test.c
      substituteInPlace mbox-nested-input-test.c \
        --replace-fail '../programs/mbox/nested-input.c' 'backend/wayland/nested-input.c'
    '';
    doCheck = true;
    postCheck = (old.postCheck or "") + ''
      $CC -Wall -Wextra -Wno-unused-parameter -Wno-unused-function \
        -I../include -Iinclude -I.. -Iprotocol -I../protocol \
        $(pkg-config --cflags wayland-client wayland-server xkbcommon pixman-1 libdrm) \
        ../mbox-nested-input-test.c protocol/text-input-unstable-v3-protocol.c \
        -L. -lwlroots-0.20 \
        $(pkg-config --libs wayland-client wayland-server xkbcommon) \
        -o mbox-nested-input-test
      LD_LIBRARY_PATH="$PWD:''${LD_LIBRARY_PATH:-}" ./mbox-nested-input-test
      cp ${../../tests/mbox-caret.c} mbox-caret-test.c
      substituteInPlace mbox-caret-test.c \
        --replace-fail '../programs/mbox/caret.h' 'caret.h'
      $CC -D_GNU_SOURCE -Wall -Wextra -I../backend/wayland \
        mbox-caret-test.c -lm -o mbox-caret-test
      ./mbox-caret-test
      $CC -Wall -Wextra -Wno-unused-parameter \
        -I../include -Iinclude -I.. -Iprotocol -I../protocol \
        $(pkg-config --cflags wayland-client wayland-server xkbcommon pixman-1 libdrm) \
        ../mbox-caret-protocol-test.c protocol/text-input-unstable-v3-protocol.c \
        -L. -lwlroots-0.20 \
        $(pkg-config --libs wayland-client wayland-server) -o mbox-caret-protocol-test
      LD_LIBRARY_PATH="$PWD:''${LD_LIBRARY_PATH:-}" ./mbox-caret-protocol-test
    '';
  });
  nestedSway = (pkgs.sway-unwrapped.override { wlroots_0_20 = nestedWlroots; }).overrideAttrs (old: {
    patches = (old.patches or []) ++ [ ../../patches/mbox-sway-caret.patch ];
    postPatch = (old.postPatch or "") + ''
      cp ${../../programs/mbox/sway-caret.c} sway/input/sway-caret.c
      cp ${../../programs/mbox/caret.h} sway/input/caret.h
      cp ${../../programs/mbox/sway-caret-state.h} sway/input/sway-caret-state.h
    '';
  });

  guestImAuto = pkgs.runCommandCC "mbox-gtk-im" {
    nativeBuildInputs = [ pkgs.pkg-config pkgs.wayland-scanner ];
    buildInputs = [ pkgs.gtk3 pkgs.wayland ];
  } ''
    mkdir -p $out/lib
    # Rebuild only GTK's Wayland IM module against the app's GTK ABI. Do not
    # replace Firefox, GTK itself, or the host input-method configuration.
    tar -xf ${pkgs.gtk3.src} --strip-components=1
    patch --fuzz=0 -p1 < ${../../patches/mbox-gtk-caret-commit.patch}
    cp modules/input/imwayland.c im-mbox-wayland.c
    substituteInPlace im-mbox-wayland.c \
      --replace-fail '#include "config.h"' '#define GETTEXT_PACKAGE "gtk30"' \
      --replace-fail '#include "gtk/gtkintl.h"' '#define GTK_LOCALEDIR ""' \
      --replace-fail 'NC_("input method menu", "Wayland")' '"Monkeybox Wayland"' \
      --replace-fail '"gdk/wayland/gdkwayland.h"' '<gdk/gdkwayland.h>' \
      --replace-fail '"wayland"' '"mbox-wayland"' \
      --replace-fail 'GtkIMContextWayland' 'GtkIMContextMboxWayland'
    protocol=${pkgs.wayland-protocols}/share/wayland-protocols/unstable/text-input/text-input-unstable-v3.xml
    wayland-scanner client-header "$protocol" text-input-unstable-v3-client-protocol.h
    wayland-scanner private-code "$protocol" text-input-protocol.c
    imFlags=$(pkg-config --cflags --libs gtk+-3.0 wayland-client)
    $CC -shared -fPIC -I. im-mbox-wayland.c text-input-protocol.c \
      $imFlags -o $out/lib/im-mbox-wayland.so
    $CC -shared -fPIC -Wall -Wextra -Wno-unused-parameter \
      ${../../programs/mbox/im-auto.c} $imFlags -o $out/lib/im-mbox.so
    # Keep GTK's standard modules available, plus the backend-aware selector.
    cat ${pkgs.gtk3}/lib/gtk-3.0/3.0.0/immodules.cache > $out/immodules.cache
    printf '\n"%s/lib/im-mbox.so"\n"mbox" "Monkeybox" "gtk30" "" ""\n' "$out" >> $out/immodules.cache
    printf '\n"%s/lib/im-mbox-wayland.so"\n"mbox-wayland" "Monkeybox Wayland" "gtk30" "" ""\n' "$out" >> $out/immodules.cache
  '';

  guestXim = pkgs.runCommandCC "mbox-xim" {
    nativeBuildInputs = [ pkgs.pkg-config ];
    # xcb-imdkit.pc Requires xcb-util, which is not propagated by its package.
    buildInputs = [ pkgs.xcb-imdkit pkgs.libxcb pkgs.libxcb-util ];
  } ''
    mkdir -p $out/bin
    # Keep dependency discovery separate so pkg-config failure stops the build.
    ximFlags=$(pkg-config --cflags --libs xcb-imdkit xcb)
    $CC -D_GNU_SOURCE -Wall -Wextra -Wno-unused-parameter \
      -I${../../programs/mbox} ${../../programs/mbox/xim-guest.c} \
      $ximFlags -o $out/bin/mbox-xim
  '';

  clipboardDesktop = pkgs.writeShellScript "mbox-clipboard-desktop" (builtins.replaceStrings
    [ "@@SWAY@@" "@@SATELLITE@@" "@@FONTCONFIG@@" "@@FONTCONFIG_FALLBACK@@" "@@XIM@@" "@@IM_CACHE@@" ]
    [ "${nestedSway}/bin/sway" "${pkgs.xwayland-satellite}/bin/xwayland-satellite" "${guestFontsConf}" "${guestFontsBase}" "${guestXim}/bin/mbox-xim" "${guestImAuto}/immodules.cache" ]
    (builtins.readFile ../../programs/mbox/clipboard-desktop.sh.in));

  clipboardSource = pkgs.writeText "mbox-clipboard.py" (builtins.replaceStrings
    [ "@@SWAYMSG@@" "@@WL_PASTE@@" "@@WL_COPY@@" "@@GUEST@@" "@@PYTHON@@" "@@WTYPE@@" ]
    [ "${pkgs.sway-unwrapped}/bin/swaymsg" "${pkgs.wl-clipboard}/bin/wl-paste"
      "${pkgs.wl-clipboard}/bin/wl-copy" "${clipboardGuest}" "${pkgs.python3}/bin/python3" "${pkgs.wtype}/bin/wtype" ]
    (builtins.readFile ../../programs/mbox/clipboard.py));
  clipboard = pkgs.writeShellScriptBin "mbox-clipboard" ''
    case "''${1:-}" in
      copy|paste|export|import|clear)
        if ${pkgs.python3}/bin/python3 ${clipboardSource} "$@"; then
          ${pkgs.python3}/bin/python3 ${clipboardSource} pulse "$1"
        else
          status=$?
          case "$status" in
            20) reason='Focus changed or no application focused' ;;
            21) reason='Unregistered, stale or non-opted-in mbox session' ;;
            22) reason='Text exceeds 1 MiB or is not supported UTF-8 text' ;;
            23) reason='Transfer buffer expired, empty or already consumed' ;;
            *) reason='Clipboard endpoint failed or timed out' ;;
          esac
          ${pkgs.libnotify}/bin/notify-send -u normal -t 4000 'mbox clipboard' "$reason" 2>/dev/null || true
          exit "$status"
        fi ;;
      *) exec ${pkgs.python3}/bin/python3 ${clipboardSource} "$@" ;;
    esac
  '';

  # Only the private bridge uses this build. Host playback policy may move its
  # tunnel to a new output; microphone tunnels retain their no-move restriction.
  bridgePulse = pkgs.pulseaudio.overrideAttrs (old: {
    patches = (old.patches or [ ]) ++ [ ../../patches/mbox-pulse-playback-move.patch ];
  });
  audioHost = pkgs.writeShellScript "mbox-audio-host" ''
    exec ${pkgs.python3}/bin/python3 ${../../programs/mbox/audio-host.py} \
      ${bridgePulse}/bin/pulseaudio ${pkgs.pulseaudio}/bin/pactl \
      ${pkgs.dbus}/bin/dbus-run-session "$@"
  '';

  desktopGuest = pkgs.writeShellScript "mbox-desktop-guest" (builtins.replaceStrings
    [ "@@SOCAT@@" "@@WAYPIPE@@" "@@ALSA_PLUGINS@@" "@@SATELLITE_BIN@@" "@@BASH@@" ]
    [ "${pkgs.socat}/bin/socat" "${mboxWaypipe}/bin/waypipe" "${pkgs.alsa-plugins}/lib/alsa-lib" "${guestSatellite}/bin" "${pkgs.bash}/bin/bash" ]
    (builtins.readFile ../../programs/mbox/desktop-guest.sh.in));

  cameraSource = pkgs.stdenv.mkDerivation {
    name = "mbox-camera-source";
    dontUnpack = true;
    nativeBuildInputs = [ pkgs.pkg-config ];
    buildInputs = [ pkgs.pipewire ];
    buildPhase = ''
      $CC -Wall -Wextra -Werror ${../../programs/mbox/camera-source.c} $(pkg-config --cflags --libs libpipewire-0.3) -o camera
    '';
    installPhase = "mkdir -p $out/bin; cp camera $out/bin/mbox-camera-source";
    meta.platforms = lib.platforms.linux;
  };
  cameraPython = pkgs.python3.withPackages (p: [ p.dbus-next ]);
  cameraGuest = pkgs.writeShellScript "mbox-camera-guest" ''
    exec ${cameraPython}/bin/python3 ${../../programs/mbox/camera-guest.py} \
      ${pkgs.pipewire}/bin/pipewire ${pkgs.wireplumber}/bin/wireplumber \
      ${pkgs.dbus}/bin/dbus-daemon ${cameraSource}/bin/mbox-camera-source \
      ${../../programs/mbox/camera-pipewire.conf} "$@"
  '';
  cameraHost = pkgs.writeShellScript "mbox-camera-host" ''
    exec ${pkgs.python3}/bin/python3 ${../../programs/mbox/camera-host.py} ${pkgs.ffmpeg}/bin/ffmpeg "$@"
  '';

  # No browser dependency by default. Validate configured immutable directories
  # during the build, not with a runtime guard that silently loses the repair.
  fontMounts = pkgs.runCommand "mbox-guest-font-mounts" {} ''
    ${lib.concatMapStringsSep "\n" (p: ''
      test -d ${lib.escapeShellArg p} || { echo "mbox: configured guestFontMounts directory missing" >&2; exit 1; }
    '') cfg.guestFontMounts}
    cat > "$out" <<'MOUNTS'
    ${lib.concatMapStringsSep "\n" (p: "MOUNTS+=(--mount-dir ${lib.escapeShellArg "${p}:${p}:ro,nodev,nosuid"})") cfg.guestFontMounts}
    MOUNTS
  '';

  guestMemory = pkgs.writeShellScript "mbox-guest-memory" ''
    exec ${pkgs.python3}/bin/python3 -I ${../../programs/mbox/guest-memory.py} "$@"
  '';
  managementSource = pkgs.writeText "mbox-manage.py" (builtins.replaceStrings
    [ "@@GUEST_MEMORY@@" ] [ "${guestMemory}" ]
    (builtins.readFile ../../programs/mbox/manage.py));
  management = pkgs.writeShellScriptBin "mbox-manage" ''
    exec ${pkgs.python3}/bin/python3 -I ${managementSource} "$@"
  '';

  script = pkgs.writeShellScriptBin "mbox" (builtins.replaceStrings
    [
      "#!/usr/bin/env bash"
      "@@MSB@@"
      "@@MANAGE@@"
      "@@GUEST_MEMORY@@"
      "@@SWAP_MIB@@"
      "@@SWAP_FIRMWARE@@"
      "@@SWAP_TOOLS@@"
      "@@NIX@@"
      "@@NIX_GUEST@@"
      "@@HOME_REPAIR@@"
      "@@PYTHON@@"
      "@@LOOPBACK_GUEST@@"
      "@@WAYPIPE@@"
      "@@DESKTOP_GUEST@@"
      "@@CLIPBOARD@@"
      "@@CLIPBOARD_DESKTOP@@"
      "@@AUDIO_HOST@@"
      "@@CAMERA_HOST@@"
      "@@CAMERA_GUEST@@"
      "@@FONT_MOUNTS@@"
      "@@IMAGE@@"
      "@@CPUS@@"
      "@@MEMORY@@"
      "@@ENV_BIN@@"
      "@@GUEST_BASH@@"
      "@@CACERT@@"
      "@@HOOK_FILE@@"
      "@@NET_MODE@@"
      "@@EXPOSE_TCP@@"
      "@@SHARE_STORE@@"
      "@@AGENT_WAIT@@"
      "@@PERSIST@@"
      "@@BIND@@"
      "@@BIND_RO@@"
      "@@LIBKRUNFW@@"
      "@@RUN_AS@@"
      "@@RUN_AS_CALLER@@"
      "@@BASE_IMAGE_TAR@@"
      "@@BASE_IMAGE_TAG@@"
      "@@ROOT_DISK@@"
      "@@BASHRC@@"
      "@@BASHRC_SYSTEM@@"
      "@@SHARE_PROFILE@@"
      "@@GUEST_SHELL_STORE@@"
      "@@EXCLUDE@@"
    ]
    [
      "export PATH=${lib.makeBinPath [ pkgs.coreutils pkgs.gnugrep pkgs.gnused pkgs.gawk pkgs.findutils pkgs.util-linux ]}:$PATH"
      "${cfg.package}/bin/msb"
      "${management}/bin/mbox-manage"
      "${guestMemory}"
      (toString cfg.swapMiB)
      (if cfg.swapFirmwarePath == null then "" else cfg.swapFirmwarePath)
      "${pkgs.util-linux}/bin"
      "${pkgs.nix}"
      "${nixGuest}"
      "${../../programs/mbox/repair-home-metadata.py}"
      "${pkgs.python3}/bin/python3"
      "${loopbackGuest}"
      "${mboxWaypipe}/bin/waypipe"
      "${desktopGuest}"
      "${clipboard}/bin/mbox-clipboard"
      "${clipboardDesktop}"
      "${audioHost}"
      "${cameraHost}"
      "${cameraGuest}"
      "${fontMounts}"
      cfg.image
      (toString cfg.cpus)
      cfg.memory
      "${mboxEnv}"
      cfg.guestShell
      "${pkgs.cacert}"
      "${hookFile}"
      cfg.networkMode
      (lib.concatMapStringsSep " " toString cfg.exposedTCPPorts)
      (if cfg.shareNixStore then "1" else "0")
      (toString cfg.agentWaitTicks)
      quotedList
      bindEntries
      bindReadOnlyEntries
      "${msbPackage}/lib/libkrunfw.so"
      (if cfg.runAsUser == null then "" else cfg.runAsUser)
      (if cfg.runAsCaller then "1" else "0")
      "${cfg.baseImageTar}"
      baseImageRef
      (if cfg.rootDisk == null then "" else "--root-disk=${cfg.rootDisk}")
      (if cfg.bashrc == null then "" else "${cfg.bashrc}")
      "${bashrcSystem}"
      (if cfg.shareHostProfile then "1" else "0")
      "${pkgs.bashInteractive}"
      (builtins.concatStringsSep " " (map (p: "\"${p}\"") cfg.excludeMounts))
    ]
    (builtins.readFile ../../programs/mbox/mbox.sh.in));
in
{
  options.programs.mbox = {
    managementPackage = lib.mkOption { type = lib.types.package; readOnly = true; description = "Host-only instance management and diagnostics CLI."; };
    swapFirmwarePath = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = "${pkgs.callPackage ../../packages/libkrunfw-swap { }}/lib/libkrunfw.so";
      defaultText = "Monkeybox's pinned source-built libkrunfw 5.6.1 with CONFIG_SWAP=y";
      description = ''
        Immutable compatible swap-enabled libkrunfw path, selected ONLY with -s.
        null makes -s fail explicitly and omits the swap firmware dependency.
        Baseline runs always use release firmware, regardless of this option.
      '';
    };
    swapMiB = lib.mkOption {
      type = lib.types.ints.between 64 65536;
      default = 8192;
      description = ''
        Guest disk-backed swap size in MiB when explicitly enabled with -s.
        Configuring this option alone never enables swap. Uses a private sandbox-owned
        ext4 virtual disk in MSB_HOME, with 256 MiB extra filesystem headroom.
        Keep MSB_HOME on encrypted disk storage, outside guest directory grants.
        Requires CONFIG_SWAP=y in compatible firmware (the bundled release lacks
        it). Setup runs as guest root then drops to the application identity.
        Storage remains until sandbox removal/replacement; deletion is not secure
        erasure. No zram, host swap changes, or shared-directory swapfiles.
      '';
    };
    clipboardPackage = lib.mkOption { type = lib.types.package; readOnly = true; description = "Explicit Sway clipboard broker."; };
    enable = lib.mkEnableOption "Monkeybox host installation through the optional NixOS adapter";

    fontPackages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = with pkgs; [ noto-fonts noto-fonts-cjk-sans noto-fonts-color-emoji ];
      description = "Fonts copied into the guest image, in addition to DejaVu. No host home is shared.";
    };

    fontConfigIncludes = lib.mkOption {
      type = lib.types.listOf lib.types.path;
      default = [];
      description = "Immutable fontconfig rendering/family rules; must not reference host-only font/cache directories.";
    };

    guestFontMounts = lib.mkOption {
      type = lib.types.listOf (lib.types.strMatching "/nix/store/[^:\n]+/.+");
      default = [];
      description = ''
        Immutable font directories given separate same-path read-only mounts
        whenever shareNixStore is enabled, including headless runs. This avoids
        aggregate-store statfs stalls for configured browsers without changing
        fonts or profiles. Missing directories fail the wrapper build. The
        desktop configuration, not this module, selects browser packages.
      '';
    };

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../../packages/microsandbox { };
      defaultText = "microsandbox 0.7.3 built from source with pinned libkrun PR #171";
      description = ''
        The microsandbox CLI and embedded agent, built from source with the
        pinned vsock TX-chain/credit fix. The wrapper retains the original
        release firmware independently of this CLI override.
      '';
    };

    wrapper = lib.mkOption {
      type = lib.types.package;
      default = script;
      description = ''
        The mbox command. Exposed so it can be built and inspected on its own, which
        is how the argument assembly is tested without KVM or an image.
      '';
    };

    runAsCaller = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Run the sandbox as the person who invoked it rather than as root. The identity is
        resolved at run time, so a shared machine gets each caller's own uid.

        This is not cosmetic: the mounts already present every file as owned by the caller,
        so a root process inside the guest sees a home directory owned by somebody else and
        programs react to that. pip is the common one — it declares the cache unusable, and
        then every install re-downloads its wheels — and a shell prompt built from `id` says
        `root` and ends in `#`. The ids themselves are unchanged either way, because the
        mounts carry them regardless.

        msb looks the user up in the image's /etc/passwd and refuses to boot otherwise, so
        the wrapper generates one containing root and the caller and mounts it over the
        guest's; the base image keeps only a root entry, since nothing else in it needs an
        account.
      '';
    };

    runAsUser = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "1000:100";
      description = ''
        Run the sandbox as this uid:gid instead of the caller, which programs.mbox.runAsCaller
        otherwise arranges. Takes precedence when set, and still needs a matching entry in the
        generated passwd file — that file is written from the caller's own ids, so naming a
        different user here means the guest will not resolve it. Use it for a base image that
        ships its own accounts.
      '';
    };

    image = lib.mkOption {
      type = lib.types.str;
      default = baseImageRef;
      defaultText = "a root filesystem built by this module and imported into msb's cache";
      description = ''
        What the guest mounts as /. By default this is an almost-empty rootfs produced
        by `dockerTools.buildImage` and imported by the wrapper on first run, so there is
        nothing to pull and no registry involved. It carries /bin/sh and /bin/bash, and
        nothing else that the sandbox uses: the interpreter and every tool arrive through
        the read-only /nix/store mount. Set it to an ordinary image reference
        (python:3.12, ghcr.io/...) to boot something real instead; that image is fetched
        from its registry on first use, as usual.
      '';
    };

    baseImageTar = lib.mkOption {
      type = lib.types.path;
      default = baseImage;
      description = ''
        The archive the built-in image is imported from. Only consulted while
        programs.mbox.image still names the built-in image.
      '';
    };

    cpus = lib.mkOption {
      type = lib.types.int;
      default = 4;
      description = "vCPUs per sandbox.";
    };

    memory = lib.mkOption {
      type = lib.types.str;
      default = "8192M";
      description = ''
        Memory per sandbox, as msb sizes it. This is RAM committed to the VM, so a
        large value is a real reservation on a machine that is already feeding an
        inference model — unlike sbox, where memory limits are cgroup advice.
      '';
    };

    networkMode = lib.mkOption {
      type = lib.types.enum [
        "open"
        "blocked"
        "host"
      ];
      default = "blocked";
      description = ''
        Default egress policy. CLI -i/--internet enables open egress. `open` gives the guest public internet access;
        `host` is a compatibility alias for `open`, not unrestricted host access.
        `blocked` denies internet access. Explicit exposedTCPPorts and CLI -p
        grants apply in every mode, including blocked. Host services are reached
        through the guest's default gateway, not its own loopback address.
        Other host ports and private networks are not granted by these profiles.
      '';
    };

    exposedTCPPorts = lib.mkOption {
      type = lib.types.listOf lib.types.port;
      default = [ ];
      description = ''
        Ports of services listening on the host that the guest may reach. Each becomes
        one guest-egress TCP allow rule; it does not enable the broad host profile.
        This is like sbox's -p, NOT sbox's --expose-port (guest port publishing).
        The mbox CLI retains --expose-port as a legacy synonym for -p.
      '';
    };

    userlandPackages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = userlandDefaults;
      defaultText = "[ coreutils findutils gnugrep gnused gawk diffutils which file less procps gnutar gzip bzip2 xz ]";
      description = ''
        Command-line tools placed on the guest's PATH ahead of the base image's busybox.
        Left empty, busybox answers `ls`, `grep`, `find` and the rest, with its habit of
        printing '?' for anything outside its built-in tables.
      '';
    };

    packages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [ ];
      description = ''
        Additional packages on PATH inside the guest; empty by default.
        A Python environment should be
        one environment, not several: the venv layered on top takes its interpreter
        from a single base, and with two envs on PATH the second one's packages
        simply vanish.
      '';
    };

    persist = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = ''
        Paths to back with per-project storage under
        ''${XDG_STATE_HOME}/mbox/projects/PROJECT/persist. Entries may use $HOME,
        which is expanded on the host; the guest's home is mounted at the same path,
        so the two agree without configuration.
      '';
    };

    bind = lib.mkOption {
      type = with lib.types; attrsOf (submodule { options.enable = lib.mkEnableOption ""; });
      # Explicit same-path grants; no inherited host configuration.
      default = { };
      description = ''
        Host directories mounted read-write into the guest at the same path. Values
        are ignored; only the keys matter.
      '';
    };

    bindReadOnly = lib.mkOption {
      type = with lib.types; attrsOf (submodule { options.enable = lib.mkEnableOption ""; });
      default = { };
      description = ''
        As bind, mounted read-only. Note that a host sysfs mounted over the guest's
        own /sys shows the host's hardware rather than the VM's, which is what you
        want for reading device capabilities and not what you want for anything else.
      '';
    };

    bashrc = lib.mkOption {
      type = with lib.types; nullOr path;
      default = null;
      description = ''
        File mounted read-only as the guest's ~/.bashrc, so the shell inside a sandbox has
        the same functions, aliases and prompt as the one outside. Mounted rather than
        written into the guest home, because that home is a per-project state directory and
        a copy in it would go stale without anything noticing. Null mounts nothing, which is
        what a sandbox booted from an unrelated base image wants.

        Read the file you point this at before doing so: it runs inside the VM, where a
        Wayland clipboard tool, a host path that is not mounted, or an alias naming a
        command the guest lacks each fails in its own way rather than the host's.
      '';
    };

    guestShell = lib.mkOption {
      type = lib.types.str;
      default = "${pkgs.bashInteractive}/bin/bash";
      description = ''
        The shell the guest starts and the interpreter the command is run with. It has to
        be a path msb can find on the host as well as in the guest, which rules out the
        obvious choices: a store path is the only kind that exists identically on both
        sides, since the guest sees the host's store. `/bin/bash` fails, and so does a
        path that has been garbage-collected — msb reports either as "sandbox process
        exited (exit status: 0) before agent relay became available", which reads like a
        kernel or image problem and says nothing about the shell. The wrapper checks the
        path before launching rather than leaving that message to be deciphered.
      '';
    };

    shellHook = lib.mkOption {
      type = lib.types.lines;
      default = "";
      description = ''
        Sourced by the guest before the command or interactive shell starts, from
        /etc/mbox-shellhook.bash. The sbox hook's venv logic is written for a bind
        mount at $HOME/.venv and stays correct here for the same reason: the persist
        mount is a mount point either way, and a mount point cannot be unlinked.
      '';
    };

    excludeMounts = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "/sys" "/proc" "/dev" ];
      description = ''
        Host paths not to mount over the guest's own, even where configured binds ask for
        them. These three belong to whichever kernel is running, so mounting the host's
        copy is wrong twice over inside a VM: it describes the wrong machine, and it
        hides devices that only exist in the guest. The second part is not cosmetic —
        a read-only bind might include /sys so that host hardware can be
        inspected, and doing that in a microVM covers up /sys/class/virtio-ports, where
        the guest agent's console lives. The sandbox then boots, cannot find its own
        device, exits with status 0, and msb reports "agent relay became available"
        failing, which points at the kernel rather than at a mount.
      '';
    };

    guestLibraryPath = lib.mkOption {
      type = lib.types.listOf lib.types.path;
      default = [
        pkgs.stdenv.cc.cc.lib
        pkgs.glibc
        pkgs.zlib
        pkgs.openssl
        pkgs.ncurses
        pkgs.libffi
      ];
      defaultText = ''[ gcc's lib; glibc; zlib; openssl; ncurses; libffi ]'';
      description = ''
        Store paths whose `lib` directories the guest's dynamic loader is told to search.
        This exists because a wheel installed by pip has no RUNPATH of its own and the
        guest has no /etc/ld.so.cache: every library named in its NEEDED entries has to
        be findable by directory, or it fails at import with "cannot open shared object
        file" even though the package installed cleanly. The default covers what wheels
        from PyPI tend to reference — libstdc++ and libgcc_s (gcc), libc and the dynamic
        pieces (glibc), libz (zlib), libssl and libcrypto (openssl), libtinfo for
        readline (ncurses), and libffi for ctypes-style code. A wheel that needs anything
        else should add it here rather than grow special cases elsewhere.
      '';
    };

    rootDisk = lib.mkOption {
      type = with lib.types; nullOr str;
      default = null;
      example = "tmpfs:1G";
      description = ''
        msb's `--root-disk` spec for the sandbox's writable layer. Unset, msb creates a
        sparse 8G image per sandbox, which is work done on every invocation because the
        wrapper recreates the sandbox each command. `tmpfs:1G` skips the file at the cost
        of counting guest writes against RAM, so keep it clear of `''${TMPDIR}`-heavy
        work: pip unpacks wheels into /tmp inside the sandbox, and a 2G torch wheel wants
        a sizeable tmpfs. The venv itself is not affected, being a virtiofs mount.

        A tmpfs root disk is charged against the sandbox's own memory, and msb refuses a
        size larger than it — measured, with msb's default of 512 MiB rejecting
        `tmpfs:1G` outright. The figure to compare against is programs.mbox.memory, which
        the wrapper passes, rather than msb's default.
      '';
    };

    shareHostProfile = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Put the host's own package profile on the guest's PATH, read-only. This is how a
        program that is installed on the machine but not declared in programs.mbox.packages
        still runs in a sandbox: nothing is copied, because the profile is a store path and
        the guest already sees the whole store. Resolved at run time rather than build time,
        since the profile is a symlink that moves on every rebuild.

        Turn it off when the point of the sandbox is that the guest has only what was
        declared; with it on, whatever the host happens to have installed is reachable,
        which is convenient for one-off commands and a hole in reproducibility for anything
        the project depends on.
      '';
    };


    shareNixStore = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Mount the host's /nix/store read-only. Turning this off means the base image
        has to provide everything itself, including the interpreter the hook looks for.
      '';
    };

    agentWaitTicks = lib.mkOption {
      type = lib.types.int;
      default = 60;
      description = ''
        How long to wait for a freshly created sandbox's agent to answer (each tick is
        0.3s) before running the command anyway.
      '';
    };

  };

  config.programs.mbox.clipboardPackage = clipboard;
  config.programs.mbox.managementPackage = management;
}
