Private Aquamarine test client
=============================
Upstream: https://github.com/hyprwm/aquamarine
Tag: v0.16.0
Commit: b77d67648336223a6c8fb88844ae316e7b72b574

This library is only for a private child Hyprland used to test Ztype. No system
Aquamarine library or Hyprland binary is replaced. Server-advertised versions
remain unchanged.

protocol-negotiation.patch caps each client registry bind at the advertised
version. It rejects protocols older than the requests the backend actually
uses: compositor 4, seat 5, xdg-shell 1, shm 1 and linux-dmabuf 4.

The installed hyprwayland-scanner 0.4.6 generates an unconditional wl_shm.release
destructor from Wayland 1.26 XML. That request starts at version 2. The separate
generated-shm-release.patch / guard-generated-shm.py makes version-1 teardown
destroy only the local proxy. It does not send an unnegotiated request.

Reproduce with the official tag and the same development dependencies:
1. Apply protocol-negotiation.patch to src/backend/Wayland.cpp.
2. Configure with CMake/Ninja, Release build, a private build directory.
3. Build the aquamarine target (this generates client protocol bindings).
4. Run guard-generated-shm.py on source/protocols/wayland.cpp.
5. Rebuild only aquamarine and inspect ldd -r.
6. Set LD_LIBRARY_PATH only for the private child Hyprland. Use the immutable
   runtime directory recorded in build-metadata.json. Do not export it globally
   or install this library into /usr/lib.

All source edits are client-side. Logs, patches, SHA-256 values, minimum-request
audit and before/after hashes of the system binaries are beside this file.
Native acceptance must still verify that the private compositor starts, maps
real windows, accepts real input, and exits cleanly.
