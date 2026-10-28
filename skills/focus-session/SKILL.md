---
name: focus-session
description: Use when the person wants to focus, concentrate, do deep work, start a pomodoro, or have no distractions for a while.
---

# Focus session

1. Ask nothing unless it is unclear what they are working on; default 25 minutes.
2. `focus_start(minutes, keep)` with `keep` = words from the titles of the windows they need
   (the document or app they named). It minimises the rest and opens the notification settings
   so they can switch on Do not disturb themselves.
3. `schedule_reminder` for the end: "Focus time is up -- shall I bring your windows back?"
4. Say one line: "Focus mode for 25 minutes. I'll tell you when it's done."
5. When they say they are done (or after the reminder): `focus_end()` restores the windows.

Pitfalls
- Keep the window they are working in -- if in doubt, keep the foreground one (`get_foreground_app`).
- Stay quiet during the session: no commentary, no suggestions.
