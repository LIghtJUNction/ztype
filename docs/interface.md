# Compact candidate window

The current interface follows a compact input-method candidate strip: one line for the composition, one line for candidates and a small third line for feedback. It requests a 520 × 112 client area. It has no large heading, framed input card or primary send button.

## Before and after

### First interface

[First native interface](evidence-archive-20261120.md)

### Current interface

[Compact native candidate strip](evidence-archive-20261120.md)

These are captures of real native windows on the isolated test display. The shared zui fix at `5e37fcfb791eec9ac523fc19373e95d0a513112f` removes the inherited 800 × 560 minimum and the unrelated application identity. The test reads actual X11 properties and requires a 520 × 112 minimum with WM_CLASS `ztype` / `Ztype`. It does not claim that every window manager or compositor has been tested.

## Decisions

| Change | Result |
| --- | --- |
| Unframed composition line | The text is the primary content; the caret shows the real append/backspace position |
| Candidate widths measured from their text | Short choices stay close together; overflow uses page arrows, and Tab reaches all nine candidates |
| A single green selection accent | The chosen text is clear without turning every candidate into a large card |
| Small language label and Pinyin example | Default Pinyin is visible; an empty composition explains `nihao → 你好` |
| Target and keyboard help under ⋯ | The main strip stays small; one click reveals the explicit destination and focus behavior |
| Enter commits; Ctrl+C copies | The main flow has no large action row; a copy action remains in the help area |
| Invalid drafts expose no committable candidates | Mouse and keyboard actions cannot send stale text |

Candidate numbers indicate order, not number-key shortcuts. Long candidate labels are shortened visually; copying and submission keep the complete UTF-8 text. The input currently supports appending and backspacing, not arbitrary cursor movement.

[Native candidate paging](evidence-archive-20261120.md)

[Small target and keyboard help](evidence-archive-20261120.md)

## Interaction evidence

Nine native checks compare the actual GTK receiver text. They cover multilingual delivery, a real plugin, panel Enter, mouse paging, Ctrl+A replacement, actual Ctrl+C-to-GTK clipboard paste, and protection against invalid or empty drafts. Opening help and pressing Escape preserves the composition before Enter submits it.

The compact fixture's nine-entry dictionary fits five entries on its first page; clicking next and pressing Enter delivers the sixth candidate. A focused XTest keyboard path is used on the private display. A direct-XSendEvent diagnostic is retained separately as failed fixture evidence.

The interface remains an application-level composition tool. It does not register a system-wide IME, guess a destination, alter input-method settings or duplicate the shared libraries' OS bindings.
