"""Everyday desktop helpers (everyday_tools.py) and the file index (file_index.py)."""
import everyday_tools as ev
import file_index
from core.effects import Effect as E, Surface as S
from core.registry import ARR, BOOL, INT, STR, register
from core.toolset.desktop import _short

# ---- finding files ---------------------------------------------------------------------
register("find_files", "Find the person's files instantly, through Windows' own search index (everything "
         "indexed: the user's folders, OneDrive, added locations) or Eva's index of Desktop, Documents, Downloads, "
         "Pictures, Videos, Music. Try this before search_files. kind: document, image, video, audio, archive, "
         "program, code. newest=true lists the most recent first ('my latest download'). contents='words' finds "
         "documents that CONTAIN those words ('the PDF that mentions the lease').",
         {"query": STR("Words from the file name."), "kind": STR(), "limit": INT(), "newest": BOOL(),
          "contents": STR("Words inside the file (needs Windows Search).")},
         surface=S.FILES, effect=E.OBSERVE, fn=file_index.find_files, verb="search", deadline=15,
         describe=lambda a: f"Finding {_short(a.get('query') or a.get('kind'), 30)}")

# ---- documents (new files only; originals are never changed) --------------------------
register("merge_pdfs", "Join PDF files, in the order given, into one new PDF beside the first.",
         {"paths": ARR(desc="Full paths, in order."), "output_name": STR("Optional name for the new file.")},
         ["paths"], surface=S.FILES, effect=E.MODIFY, fn=ev.merge_pdfs, verb="write", deadline=120,
         describe=lambda a: "Merging the PDFs")
register("split_pdf", "Copy chosen pages of a PDF ('1-3, 7') into a new PDF beside it.",
         {"path": STR(), "pages": STR("For example 1-3, 7")}, ["path", "pages"],
         surface=S.FILES, effect=E.MODIFY, fn=ev.split_pdf, verb="write", deadline=120,
         describe=lambda a: "Taking out those pages")
register("word_to_pdf", "Save a Word document as a PDF beside it (needs Microsoft Word installed).",
         {"path": STR()}, ["path"], surface=S.FILES, effect=E.MODIFY, fn=ev.word_to_pdf, verb="write",
         deadline=180, describe=lambda a: "Saving it as a PDF")
register("pdf_tables_to_csv", "Pull table-like rows out of a PDF into a CSV beside it (opens in Excel).",
         {"path": STR()}, ["path"], surface=S.FILES, effect=E.MODIFY, fn=ev.pdf_tables_to_csv, verb="write",
         deadline=120, describe=lambda a: "Pulling out the tables")

# ---- PC care (reports only) --------------------------------------------------------------
register("disk_space", "Free and used space on each drive; flags drives over 90% full.",
         surface=S.SYSTEM, effect=E.OBSERVE, fn=ev.disk_space, verb="look", deadline=20,
         describe=lambda a: "Checking disk space")
register("largest_files", "The biggest files in a folder (default: the person's home), to free space.",
         {"folder": STR(), "limit": INT()}, surface=S.FILES, effect=E.OBSERVE, fn=ev.largest_files,
         verb="search", deadline=45, describe=lambda a: "Looking for big files")
register("duplicate_files", "Groups of identical files in a folder and the space they waste. Deletes nothing.",
         {"folder": STR(), "limit": INT()}, ["folder"], surface=S.FILES, effect=E.OBSERVE,
         fn=ev.duplicate_files, verb="search", deadline=120, describe=lambda a: "Looking for duplicates")
register("startup_apps", "Programs that start with Windows.", surface=S.SYSTEM, effect=E.OBSERVE,
         fn=ev.startup_apps, verb="look", deadline=15)
register("battery_status", "Battery charge, whether it is plugged in, and time left.",
         surface=S.SYSTEM, effect=E.OBSERVE, fn=ev.battery_status, verb="look", deadline=10)

# ---- email and calendar: drafts the person sends or saves themselves --------------------
register("draft_email", "Open a new email in the person's mail app, already written. Nothing is sent: "
         "they review it and press Send.",
         {"to": STR(), "subject": STR(), "body": STR(), "cc": STR()}, surface=S.SYSTEM, effect=E.INTERACT,
         fn=ev.draft_email, verb="write", deadline=20, describe=lambda a: "Writing the email draft")
register("add_calendar_event", "Put an event in the person's calendar app for them to save. start is "
         "'YYYY-MM-DD HH:MM' local time; minutes is the length.",
         {"title": STR(), "start": STR(), "minutes": INT(), "location": STR(), "notes": STR()},
         ["title", "start"], surface=S.SYSTEM, effect=E.INTERACT, fn=ev.add_calendar_event, verb="write",
         deadline=20, describe=lambda a: f"Adding {_short(a.get('title'), 30)} to the calendar")

# ---- window layouts and focus ----------------------------------------------------------
register("save_layout", "Remember where every open window is, under a name ('coding', 'writing').",
         {"name": STR()}, ["name"], surface=S.WINDOW, effect=E.OBSERVE, fn=ev.save_layout, verb="look",
         deadline=30, describe=lambda a: f"Remembering the {_short(a.get('name'), 20)} layout")
register("restore_layout", "Put windows back as a saved layout had them, starting apps that are not running.",
         {"name": STR()}, ["name"], surface=S.WINDOW, effect=E.INTERACT, fn=ev.restore_layout,
         verb="arrange", deadline=90, describe=lambda a: f"Setting up the {_short(a.get('name'), 20)} layout")
register("list_layouts", "The saved window layouts.", surface=S.WINDOW, effect=E.OBSERVE,
         fn=ev.list_layouts, verb="look")
register("focus_start", "Start a focus session: minimise every window except the ones to keep (words from "
         "their titles, comma-separated) and open the Do-not-disturb settings. Pair it with schedule_reminder "
         "for the end.", {"minutes": INT(), "keep": STR()}, surface=S.WINDOW, effect=E.INTERACT,
         fn=ev.focus_start, verb="tidy", deadline=60, describe=lambda a: "Clearing the desk for focus")
register("focus_end", "End the focus session: bring back the windows it minimised.",
         surface=S.WINDOW, effect=E.INTERACT, fn=ev.focus_end, verb="arrange", deadline=60,
         describe=lambda a: "Bringing your windows back")
