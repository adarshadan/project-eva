"""Skills, fast internet reading, and undo. Declared once each; see
core/registry.py. save_skill is bound by the agent (it needs to know whether
the current task read third-party text)."""
from core import checkpoints, skills, web_reach
from core.effects import Effect as E, Surface as S
from core.registry import ARR, INT, STR, register
from core.toolset.desktop import _short

# ---- tool groups (core/toolbox.py); bound by the agent, which keeps the open groups ----
register("use_tools", "Open one or more tool GROUPS from the MORE TOOLS index, so their tools are in your list "
         "for this task. Open only what the task needs.",
         {"groups": ARR("string", "Group names from the index, e.g. [\"windows\"].")}, ["groups"],
         surface=S.SELF, effect=E.OBSERVE, verb="none", embodied=False,
         describe=lambda a: "Getting more tools ready")

# ---- skills ---------------------------------------------------------------------
register("load_skill", "Read a skill from the SKILLS catalogue: the procedure for a kind of task. Call it "
         "before starting a task the catalogue covers, then follow it.",
         {"name": STR("The skill's name from the catalogue.")}, ["name"],
         surface=S.SYSTEM, effect=E.OBSERVE, fn=skills.load_skill, verb="read",
         describe=lambda a: f"Recalling how to {_short(str(a.get('name', '')).replace('-', ' '))}")
register("find_skill", "Search all skills, including the library of about 480 (documents, finance, research, "
         "writing, design, coding, marketing, expert personas...), by what the task is about. Then load_skill "
         "the best match.",
         {"query": STR("A few words about the task, e.g. 'excel financial model' or 'proofread essay'."),
          "limit": INT("Default 8."), "category": STR("Optional: a category name, or 'personas'.")}, ["query"],
         surface=S.SYSTEM, effect=E.OBSERVE, fn=skills.find_skill, verb="search",
         describe=lambda a: f"Looking for a skill: {_short(a.get('query'), 30)}")
register("read_skill_file", "Read one support file of a skill (a path listed in load_skill's files).",
         {"name": STR(), "path": STR("For example references/notes.md")}, ["name", "path"],
         surface=S.SYSTEM, effect=E.OBSERVE, fn=skills.read_skill_file, verb="read")
register("save_skill", "Save how to do a kind of task so you do it right next time: when the person asks you "
         "to remember a procedure, or corrects how you did something. name is the class of task "
         "('send-weekly-report'), description says when to use it, instructions are the steps, tools, "
         "pitfalls and the person's preferences. Re-using a name improves that skill.",
         {"name": STR("1-64 lowercase letters, digits and hyphens."), "description": STR(),
          "instructions": STR("Markdown: steps in order, what worked, pitfalls, preferences.")},
         ["name", "description", "instructions"],
         surface=S.SYSTEM, effect=E.MODIFY, verb="write", describe=lambda a: "Learning how to do this")

# ---- reading the internet -----------------------------------------------------------
register("read_web_page", "Read a public web page as clean text, fast, without opening a browser. Best for "
         "articles, docs and search results pages. If it fails (login, human check, interactive page), "
         "use browser_open instead.",
         {"url": STR()}, ["url"],
         surface=S.BROWSER, effect=E.OBSERVE, fn=web_reach.read_web_page, deadline=75, verb="read", web=True,
         describe=lambda a: "Reading the page")
register("youtube_transcript", "Get the spoken words of a YouTube video (its captions) to summarise or "
         "answer questions about it.",
         {"url": STR("The video's address or id."), "language": STR("Preferred caption language, default en.")},
         ["url"], surface=S.BROWSER, effect=E.OBSERVE, fn=web_reach.youtube_transcript, deadline=60,
         verb="read", web=True, describe=lambda a: "Getting the video's transcript")
register("read_feed", "Newest items of an RSS or Atom feed (news, blogs, podcasts): titles, links, dates.",
         {"url": STR(), "limit": INT("Default 10, at most 30.")}, ["url"],
         surface=S.BROWSER, effect=E.OBSERVE, fn=web_reach.read_feed, deadline=45, verb="read", web=True,
         describe=lambda a: "Reading the feed")

# ---- undo -------------------------------------------------------------------------------
register("list_changes", "Files Eva recently wrote, overwrote, created or moved, newest first, with ids for "
         "undo_last_change.", {"limit": INT()},
         surface=S.FILES, effect=E.OBSERVE, fn=checkpoints.list_changes, verb="look")
register("undo_last_change", "Undo a file change Eva made: restores an overwritten file, removes one she "
         "created (a copy is kept), or moves a file back. Without change_id it undoes the most recent one.",
         {"change_id": STR("Optional, from list_changes.")},
         surface=S.FILES, effect=E.MODIFY, fn=checkpoints.undo_change, verb="tidy",
         describe=lambda a: "Undoing my last change")

# ---- helpers (background workers, core/workers.py) ---------------------------------------
# Bound at startup to the live Helpers instance. embodied=False: main.py shows
# the hand-off itself (a delegating gesture and a helper light beside her).
register("delegate", "Hand a background task to a helper so you stay free: research, reading or "
         "summarising files and pages, comparing documents, drafting a text or report. A helper cannot "
         "use the screen, mouse or keyboard or change the person's files -- never delegate on-screen "
         "work. Up to 3 helpers at once. Returns at once; the report arrives later as a [system] note.",
         {"task": STR("The whole task in one or two sentences: what to find or make, and what for.")}, ["task"],
         surface=S.SYSTEM, effect=E.OBSERVE, verb="send", embodied=False,
         describe=lambda a: "Handing this to a helper")
register("helpers_status", "What each helper is doing or has finished, with their reports.",
         surface=S.SYSTEM, effect=E.OBSERVE, verb="look", embodied=False)
register("cancel_helper", "Stop one helper (by number) or, without a number, all of them.",
         {"helper": INT("The helper's number; leave out to stop all.")},
         surface=S.SYSTEM, effect=E.OBSERVE, verb="close", embodied=False)

