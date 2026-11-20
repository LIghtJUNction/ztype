#!/usr/bin/env python3
"""Additional native popup assertions for the owned Wayland GTK fixture.

Diagnostics describe protocol submission only. The caller supplies screenshots
from a verified private compositor output to establish visible rendering.
"""
from __future__ import annotations

import json
from pathlib import Path
import time


class PopupObserver:
    FIELDS = {
        "schema", "requested_visible", "selected", "candidate_count", "generation",
        "width", "height", "sequence", "presentation_result",
    }
    RESULTS = {"presented", "queued", "hidden", "unavailable", "error"}

    def __init__(self, path):
        self.path = Path(path)
        self.observations = []

    def state(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if set(value) != self.FIELDS:
            raise AssertionError("popup diagnostics contain unexpected or missing fields")
        if value["schema"] != "ztype.popup-state.v1":
            raise AssertionError("unknown popup diagnostic schema")
        if type(value["requested_visible"]) is not bool:
            raise AssertionError("popup requested_visible is not a boolean")
        for key in self.FIELDS - {"schema", "requested_visible", "presentation_result"}:
            if type(value[key]) is not int or value[key] < 0:
                raise AssertionError(f"popup diagnostic {key} is not a nonnegative integer")
        if value["presentation_result"] not in self.RESULTS:
            raise AssertionError("popup diagnostic has an unknown presentation_result")
        return value

    def wait(self, predicate, message, after=None):
        deadline = time.monotonic() + 6
        last = None
        while time.monotonic() < deadline:
            last = self.state()
            if last and (after is None or last["sequence"] > after) and predicate(last):
                self.observations.append(dict(last))
                return last
            time.sleep(0.02)
        raise AssertionError(f"{message}; last popup diagnostics: {last}")

    def sequence(self):
        state = self.state()
        return state["sequence"] if state else 0

    def visible(self, selected=None, after=None):
        return self.wait(
            lambda state: state["requested_visible"]
            and state["candidate_count"] > 0 and state["width"] > 0 and state["height"] > 0
            and state["presentation_result"] in {"presented", "queued"}
            and (selected is None or state["selected"] == selected),
            "candidate popup was not submitted for the current composition", after,
        )

    def hidden(self, after=None):
        return self.wait(
            lambda state: not state["requested_visible"]
            and state["presentation_result"] == "hidden",
            "candidate popup did not become hidden", after,
        )

    def remains_hidden(self):
        # Several compositor frames allow an outstanding wl_buffer.release.
        # This is a regression observation, not a delay used to make input work.
        for _ in range(8):
            state = self.state()
            if not state or state["requested_visible"] or state["presentation_result"] != "hidden":
                raise AssertionError("cancelled candidate popup was requested again")
            time.sleep(0.025)
        self.observations.append(dict(state))
        return state


def run_popup_checks(receiver, create_peer, focus_receiver, lua_dispatch,
                     popup, candidates, passed, capture):
    receiver.empty_normal()
    before = receiver.control("snapshot")
    sequence = popup.sequence()
    receiver.text("nihao")
    state = receiver.composed()
    shown = popup.visible(selected=0, after=sequence)
    receiver.ensure_focus()
    capture("popup-visible.png")
    state = receiver.control("snapshot")
    receiver.ensure_focus()
    if state["normal"]["focus_out_events"] != before["normal"]["focus_out_events"]:
        raise AssertionError("displaying the popup moved focus away from the application")
    passed("popup-visible-keeps-application-focus", state, popup=shown)

    receiver.key("Escape")
    receiver.committed("")
    popup.hidden()
    receiver.text("ni")
    popup.visible(selected=0)
    sequence = popup.sequence()
    receiver.key("Tab")
    popup.visible(selected=1, after=sequence)
    sequence = popup.sequence()
    receiver.shortcut("shift", "Tab")
    popup.visible(selected=0, after=sequence)
    sequence = popup.sequence()
    receiver.key("Tab")
    selected = popup.visible(selected=1, after=sequence)
    capture("popup-selected.png")
    receiver.key("space")
    state = receiver.committed(candidates[1])
    hidden = popup.hidden(after=selected["sequence"])
    passed("popup-cycle-selects-and-commits", state, selected_popup=selected, popup=hidden)

    receiver.empty_normal()
    receiver.text("ni")
    shown = popup.visible()
    receiver.text("2")
    state = receiver.committed(candidates[1])
    passed("popup-number-commit-hides", state, popup=popup.hidden(after=shown["sequence"]))

    receiver.empty_normal()
    receiver.text("nihao")
    shown = popup.visible()
    receiver.key("Escape")
    state = receiver.committed("")
    popup.hidden(after=shown["sequence"])
    hidden = popup.remains_hidden()
    capture("popup-cancelled.png")
    passed("popup-escape-clears-pending-frame", state, popup=hidden)

    receiver.text("ni")
    shown = popup.visible()
    receiver.key("BackSpace")
    receiver.key("BackSpace")
    state = receiver.committed("")
    passed("popup-delete-empty-hides", state, popup=popup.hidden(after=shown["sequence"]))

    receiver.text("nihao")
    shown = popup.visible()
    receiver.control("password")
    receiver.wait(lambda state: state["password"]["focused"], "password did not receive focus")
    hidden = popup.hidden(after=shown["sequence"])
    receiver.text("nihao")
    state = receiver.committed("nihao", "password")
    if state["normal"]["text"] or state["normal"]["preedit"]:
        raise AssertionError("sensitive field transition retained composition")
    popup.remains_hidden()
    capture("popup-password-hidden.png")
    passed("popup-password-hides", state, popup=hidden)

    receiver.empty_normal()
    receiver.text("nihao")
    shown = popup.visible()
    receiver.control("readonly")
    state = receiver.wait(
        lambda state: not state["normal"]["editable"]
        and not state["normal"]["text"] and not state["normal"]["preedit"],
        "read-only transition retained composition",
    )
    hidden = popup.hidden(after=shown["sequence"])
    receiver.text("nihao")
    receiver.key("space")
    if receiver.state()["normal"]["text"]:
        raise AssertionError("read-only field accepted synthetic text")
    popup.remains_hidden()
    passed("popup-readonly-hides", state, popup=hidden)
    receiver.control("writable")

    receiver.empty_normal()
    receiver.text("nihao")
    old = popup.visible()
    peer = None
    peer_completed = False
    try:
        peer = create_peer()
        focus_receiver(peer, lua_dispatch)
        receiver.wait(
            lambda state: not state["normal"]["focused"]
            and not state["normal"]["text"] and not state["normal"]["preedit"],
            "switching windows retained the previous composition",
        )
        popup.hidden(after=old["sequence"])
        peer.text("shijie")
        state = peer.composed()
        shown = popup.visible()
        if shown["generation"] == old["generation"]:
            raise AssertionError("new application reused the previous popup context generation")
        peer.ensure_focus()
        capture("popup-second-window.png")
        peer.key("space")
        state = peer.committed("世界")
        hidden = popup.hidden(after=shown["sequence"])
        if receiver.state()["normal"]["text"]:
            raise AssertionError("old window received a stale commit")
        passed("popup-window-change-discards", state, previous_generation=old["generation"], popup=hidden)
        peer_completed = True
    finally:
        if peer:
            peer.stop()
        # This is the explicit, expected test-window transition. An unexpected
        # focus loss during any key sequence still fails without retrying.
        if peer_completed:
            focus_receiver(receiver, lua_dispatch)

    run_external_edit_check(receiver, popup, passed, capture)


def run_external_edit_check(receiver, popup, passed, capture=None):
    receiver.empty_normal()
    receiver.text("nihao")
    shown = popup.visible()
    if capture:
        capture("popup-before-external-edit.png")
    raw_edit = receiver.control("external-edit")
    if capture:
        capture("popup-after-external-edit.png")
    receiver.control("cursor-reset")
    state = receiver.wait(
        lambda state: state["normal"]["text"] == "application-edit"
        and not state["normal"]["preedit"] and state["normal"]["focused"],
        "same-field GTK cursor reset did not cancel composition",
    )
    hidden = popup.hidden(after=shown["sequence"])
    popup.remains_hidden()
    receiver.ensure_focus()
    if state["normal"]["focus_out_events"] != raw_edit["normal"]["focus_out_events"]:
        raise AssertionError("GTK cursor reset changed the focused field")
    if capture:
        capture("popup-after-cursor-reset.png")
    receiver.text("shijie")
    receiver.key("space")
    state = receiver.committed("application-edit世界")
    popup.hidden()
    passed(
        "same-field-gtk-cursor-reset-cancels-preedit", state, popup=hidden,
        raw_edit_observation=raw_edit["normal"],
        notification="public GtkEntry move-cursor BUFFER_ENDS action",
    )
