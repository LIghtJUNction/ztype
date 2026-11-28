# XIM field purpose and focus

A GTK3 application can mark an entry as a password and hide its contents. Standard XIM exposes input-context focus, reset and preedit attributes. The locked XIM adapter has no field-purpose attribute. Its raw `sensitive` flag was false for *both* entries in a real GTK3/XWayland fixture. Ztype therefore reports an unknown purpose for this transport. Window titles, executable names and field contents do not participate in this decision. [X.Org XIM protocol](https://xorg.freedesktop.org/archive/current/doc/libX11/XIM/xim.html) · [Locked context and adapter implementation](https://github.com/LIghtJUNction/zinput/blob/da22743e1ad16bedb79751c8e8bb71e63c45d197/native/x11/src/lib.rs#L43)

| Configured policy | New or reset XIM context | Ctrl+Space in the current field |
| --- | --- | --- |
| `xim_unknown_purpose=passthrough` (default, including existing configurations) | Forward keys. No composition engine or dictionary is loaded. | Forward the shortcut. |
| `xim_unknown_purpose=manual` | Start in direct input, including when `initial_mode=chinese`. | Toggle composition for this observed focus only. Opt in only in a known ordinary field. |

XIM disables learning and popup-state recording in both modes. Manual composition can read configured dictionaries. `ztype features` exposes `xim_field_purpose=unknown` as a JSON string in XIM builds and `unavailable` without XIM. `ztype config` shows the saved policy. The frontend prints the effective policy at startup.

## Session boundary

The locked adapter advances its generation when an XIC changes, loses focus, resets, or changes focus window. Ztype scopes the engine's `ContextId` to that generation and compares each delivered key's complete context snapshot. On a changed snapshot it clears preedit, candidates and manual consent. The adapter checks the original generation and serial before submitting a commit or presenting a popup. Returning to the same widget starts a new session when the client reports the transition.

An application can reuse its current XIC without sending any focus or reset event. Ztype cannot detect a field change in that case. In manual mode an existing opt-in can therefore extend into a masked field. Keep the default policy when that distinction matters. A future adapter with a reliable purpose signal should treat a reported sensitive field as direct input and revoke any in-flight composition. A new adapter must establish its own purpose and context capabilities.

## Reproduce on a private display

Build with `cargo build --locked --no-default-features --features x11,wayland-popup --bin ztype`, then run the following with Sway (headless/pixman), Xwayland, GTK3 with PyGObject, xdotool and xauth available.

```sh
/usr/bin/python3 scripts/test_xim_purpose.py --binary target/debug/ztype
```

The test creates an isolated display with a fresh Xauthority cookie, a temporary home and no session bus. It places a normal entry and a synthetic masked entry in the same GTK3 window, starts with a composing ordinary field in manual mode, and changes focus before sending the delayed selection. It checks the mapped native popup and exact widget contents, then performs a fresh explicit selection on return. It also checks passthrough with a legacy configuration, policy readback on restart, and absence of learning files and popup-state output. The report contains the binary hash and check results. [Measured comparison](evidence/xim-purpose-20261121.json)
