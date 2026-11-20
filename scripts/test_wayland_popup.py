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


def popup_pixels(path, baseline, state, field_rect):
    """Find the popup's complete border and changed dark interior in real pixels."""
    from PIL import Image
    with Image.open(path) as loaded:
        picture = loaded.convert("RGB")
    with Image.open(baseline) as loaded:
        previous = loaded.convert("RGB")
    if picture.size != previous.size:
        raise AssertionError("private output changed size during popup observation")
    width, height = state["width"], state["height"]
    pixels, old = picture.load(), previous.load()
    field_x, field_y, field_width, field_height = field_rect
    min_x, max_x = max(0, field_x - 20), min(picture.width - width, field_x + field_width)
    min_y, max_y = max(0, field_y - height - 20), min(picture.height - height, field_y + field_height + 20)

    def near(pixel, target):
        return max(abs(pixel[index] - target[index]) for index in range(3)) <= 3

    border, background = (71, 75, 82), (32, 33, 36)
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            if not near(pixels[x, y], border) or not near(pixels[x + width - 1, y], border):
                continue
            if sum(near(pixels[x + dx, y], border) for dx in range(width)) < width * 0.95:
                continue
            if sum(near(pixels[x + dx, y + height - 1], border) for dx in range(width)) < width * 0.95:
                continue
            if sum(near(pixels[x, y + dy], border) and near(pixels[x + width - 1, y + dy], border)
                   for dy in range(height)) < height * 0.95:
                continue
            changed = sum(
                near(pixels[x + dx, y + dy], background)
                and max(abs(pixels[x + dx, y + dy][i] - old[x + dx, y + dy][i]) for i in range(3)) > 12
                for dy in range(1, height - 1) for dx in range(1, width - 1)
            )
            if changed < width * height * 0.15:
                continue
            return {"rect": [x, y, width, height], "changed_background_pixels": changed,
                    "input_rect": list(field_rect), "screenshot": Path(path).name}
    raise AssertionError(f"visible candidate rectangle {width}x{height} was not found beside input {field_rect}")


def capture_popup(capture, name, baseline, shown, receiver):
    deadline = time.monotonic() + 3
    error = None
    while time.monotonic() < deadline:
        path = capture(name)
        if path is None:
            raise AssertionError("visible popup acceptance requires private output screenshots")
        try:
            return popup_pixels(path, baseline, shown, receiver.input_rect())
        except AssertionError as observed:
            error = observed
            time.sleep(0.03)
    raise error


def run_popup_checks(receiver, create_peer, focus_receiver, lua_dispatch,
                     popup, candidates, passed, capture):
    receiver.empty_normal()
    before = receiver.control("snapshot")
    baseline = capture("popup-empty.png")
    sequence = popup.sequence()
    receiver.text("nihao")
    state = receiver.composed()
    shown = popup.visible(selected=0, after=sequence)
    receiver.ensure_focus()
    pixels = capture_popup(capture, "popup-visible.png", baseline, shown, receiver)
    state = receiver.control("snapshot")
    receiver.ensure_focus()
    if state["normal"]["focus_out_events"] != before["normal"]["focus_out_events"]:
        raise AssertionError("displaying the popup moved focus away from the application")
    passed("popup-visible-keeps-application-focus", state, popup=shown, pixels=pixels)

    original_rect = receiver.input_rect()
    receiver.key("Escape")
    popup.hidden()
    receiver.control("move-entry")
    receiver.wait(lambda state: state["normal"]["rect"][1] >= before["normal"]["rect"][1] + 90,
                  "the owned field did not move for positioning acceptance")
    moved_baseline = capture("popup-moved-empty.png")
    receiver.text("nihao")
    receiver.composed()
    moved = popup.visible(selected=0)
    moved_pixels = capture_popup(capture, "popup-follows-input.png", moved_baseline, moved, receiver)
    movement = receiver.input_rect()[1] - original_rect[1]
    if abs((moved_pixels["rect"][1] - pixels["rect"][1]) - movement) > 8:
        raise AssertionError("candidate popup did not follow the moved input cursor")
    passed("popup-follows-input-position", receiver.control("snapshot"),
           field_delta_y=movement, pixels=moved_pixels)
    receiver.key("Escape")
    popup.hidden()
    receiver.control("restore-entry")
    receiver.wait(lambda state: state["normal"]["rect"][1] == before["normal"]["rect"][1],
                  "the owned field did not restore its original position")

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
    receiver.text("ni")
    shown = popup.visible(selected=0)
    if len(candidates) < 7:
        raise AssertionError("pagination fixture requires at least seven candidates")
    receiver.key("Page_Down")
    page = popup.visible(selected=5, after=shown["sequence"])
    capture("popup-second-page.png")
    receiver.text("2")
    state = receiver.committed(candidates[6])
    passed("popup-page-relative-number-commit", state, selected_page=page, popup=popup.hidden())

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



def run_external_edit_check(receiver, popup, passed):
    """Verify the GTK client contract for replacing content during preedit."""
    receiver.empty_normal()
    receiver.text("nihao")
    shown = popup.visible()
    receiver.control("external-edit")
    state = receiver.wait(
        lambda state: state["normal"]["text"] == "application-edit"
        and not state["normal"]["preedit"] and state["normal"]["focused"],
        "application-originated ASCII edit did not cancel composition",
    )
    hidden = popup.hidden(after=shown["sequence"])
    popup.remains_hidden()
    receiver.ensure_focus()
    receiver.text("shijie")
    receiver.key("space")
    state = receiver.committed("application-edit世界")
    popup.hidden()
    passed("popup-external-edit-cancels", state, popup=hidden)
