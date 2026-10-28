---
name: pc-health-check
description: Use when the person asks how their PC is doing, why it is slow or full, to free up space, or to check battery, startup programs or duplicates.
---

# PC health check

1. **Measure, all at once:** `disk_space()`, `battery_status()`, `startup_apps()`, and `system_info()`.
2. If a drive is over 90% full, or the person wants space: `largest_files()` on their home folder and
   `duplicate_files()` on Downloads (and any folder they name).
3. **Report in two or three sentences:** the one or two things that matter most ("C: is 94% full;
   the 5 largest files in Downloads are 18 GB of videos; 11 programs start with Windows").
   Long lists go to the reply as text, which is opened in Notepad automatically.
4. **Suggest, never clean up on your own.** Offer: moving big files somewhere the person chooses
   (`move_file`, which asks them), Windows' Disk Cleanup (`launch_app('cleanmgr')`), Task Manager's
   Startup tab for startup programs (`launch_app('taskmgr')`).

Pitfalls
- Never delete anything. Duplicates: the person picks which copy goes.
- Do not judge a startup program you do not recognise as harmful; say what it is if you know, else
  that it is unfamiliar.
