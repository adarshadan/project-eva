---
name: window-layouts
description: Use when the person wants to save how their windows are arranged, set up a named workspace ("my coding setup"), or arrange windows side by side.
---

# Window layouts

- "Remember this layout as coding": `save_layout("coding")`.
- "Set up coding": `restore_layout("coding")` -- it moves the windows back and starts apps that are
  not running. Report what could not be found.
- "Which layouts do I have?": `list_layouts()`.
- Side by side right now: `get_screen_size`, then for two windows `move_window`/`resize_window` each to
  half the width (left half, right half), full height above the taskbar.
