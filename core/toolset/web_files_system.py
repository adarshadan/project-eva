"""Browser, files, system and time capabilities. Declared once each; see
core/registry.py."""
import backend_tools
import browser_agent
import desktop_control as dc
import idle_behavior
import scheduler
import system_tools
from core.effects import Effect as E, Surface as S, command_effect, write_effect
from core.registry import ARR, BOOL, INT, NUM, OBJ, STR, register
from core.toolset.desktop import _base, _short

# ---- browser --------------------------------------------------------------------
# Not gated as a whole: a search, a filter and a scroll are not purchases.
# browser_agent judges every click/type/Enter step (finance_guard) and asks
# only for the ones that spend money.
register("browser_open", "Open a URL in Eva's automation browser (then browser_act / browser_extract work "
         "on it). For a search, open the results URL directly, e.g. https://duckduckgo.com/?q=... "
         "Pass browser='brave', 'chrome' or 'edge' when the person names one.",
         {"url": STR(), "browser": STR()}, ["url"],
         surface=S.BROWSER, effect=E.INTERACT, fn=browser_agent.browser_open, verb="open", web=True,
         describe=lambda a: "Opening the page")
register("browser_act", "Do something on the open web page, described in plain words ('click the first "
         "result', 'type hello into the search box and press Enter'). Works from the page's own DOM.",
         {"instruction": STR(), "max_steps": INT("Default 6.")}, ["instruction"],
         surface=S.BROWSER, effect=E.INTERACT, fn=browser_agent.browser_act, verb="work", web=True,
         describe=lambda a: "Working in the browser")
register("browser_extract", "Read information from the page open in YOUR automation browser (after "
         "browser_open) -- not the person's own browser ('the first five headlines').",
         {"what": STR()}, ["what"],
         surface=S.BROWSER, effect=E.OBSERVE, fn=browser_agent.browser_extract, verb="read", web=True,
         describe=lambda a: "Reading the page")
register("browser_resume", "After the person dealt with a 'confirm you are human' page: check it cleared "
         "and carry on. Only needed if they say it is done and nothing resumed.",
         surface=S.BROWSER, effect=E.OBSERVE, fn=browser_agent.browser_resume, verb="read", web=True,
         describe=lambda a: "Checking the page")
register("agent_message", "POST a JSON message to another agent or service over https and return its reply.",
         {"endpoint": STR(), "payload": OBJ()}, ["endpoint", "payload"],
         surface=S.SYSTEM, effect=E.EXTERNAL, fn=browser_agent.agent_message, verb="send", web=True,
         describe=lambda a: "Sending a message")

# ---- files -------------------------------------------------------------------------
register("search_files", "Find files whose NAME contains all the query words, under a folder (default: "
         "home). Optional extensions filter, e.g. ['pdf','docx'].",
         {"query": STR(), "root": STR(), "extensions": ARR(), "max_results": INT()}, ["query"],
         surface=S.FILES, effect=E.OBSERVE, fn=system_tools.search_files, verb="search",
         describe=lambda a: f"Searching for {_short(a.get('query') or a.get('extensions'))}")
register("read_file", "Read a text, .pdf or .docx file. Long files come in parts: if the result has "
         "next_offset, call again with that offset before summarising.",
         {"path": STR(), "max_chars": INT(), "offset": INT()}, ["path"],
         surface=S.FILES, effect=E.OBSERVE, fn=system_tools.read_file, verb="read",
         describe=lambda a: f"Reading {_short(_base(a.get('path')))}")
register("list_folder", "List a folder's contents (default: home).", {"path": STR()},
         surface=S.FILES, effect=E.OBSERVE, fn=system_tools.list_folder, verb="read",
         describe=lambda a: f"Looking in {_short(_base(a.get('path')))}")
register("create_folder", "Create a folder and any missing parents.", {"path": STR()}, ["path"],
         surface=S.FILES, effect=E.MODIFY, fn=system_tools.create_folder, verb="write",
         describe=lambda a: f"Creating {_short(_base(a.get('path')))}")
register("move_file", "Move or rename a file or folder. Never overwrites.",
         {"source": STR(), "destination": STR()}, ["source", "destination"],
         surface=S.FILES, effect=E.DESTROY, fn=system_tools.move_file, verb="tidy", checkpoint=True,
         describe=lambda a: f"Moving {_short(_base(a.get('source')))}")
register("write_file", "Create, append to, or overwrite a text file directly, then read it back to "
         "confirm. Use this, not Notepad, when the goal is a FILE. mode: create (default; fails if it "
         "exists), append, overwrite.",
         {"path": STR(), "content": STR(), "mode": STR(enum=["create", "append", "overwrite"])},
         ["path", "content"],
         surface=S.FILES, effect=write_effect, fn=backend_tools.write_file, verb="write", checkpoint=True,
         describe=lambda a: f"Writing {_short(_base(a.get('path')))}")

# ---- system -----------------------------------------------------------------------
register("get_current_time", "The person's local date and time.",
         surface=S.SYSTEM, effect=E.OBSERVE, fn=dc.get_current_time)
register("get_clipboard", "Read the text on the clipboard.",
         surface=S.SYSTEM, effect=E.OBSERVE, fn=system_tools.get_clipboard, verb="look")
register("get_selected_text", "The text the person has selected in the app in front (or had selected when "
         "they pressed Eva's shortcut), plus the clipboard and how many seconds ago it changed. Use for "
         "'this', 'what I selected', 'what I just copied'.",
         surface=S.SYSTEM, effect=E.OBSERVE, fn=lambda: __import__("ubiquity").get_selected_text(), verb="look",
         deadline=10)
register("set_clipboard", "Put text on the clipboard.", {"text": STR()}, ["text"],
         surface=S.SYSTEM, effect=E.INTERACT, fn=system_tools.set_clipboard, verb="write")
register("notify", "Show a Windows desktop notification.", {"title": STR(), "message": STR()},
         ["title", "message"], surface=S.SYSTEM, effect=E.INTERACT, fn=system_tools.notify, verb="send")
register("run_command", "Run ONE program without a shell (no pipes, redirects or chaining). Read-only "
         "programs (whoami, ipconfig, tasklist, ping, git status, pip list...) run at once; anything "
         "else asks the person first.",
         {"command": STR(), "cwd": STR(), "timeout": INT("Seconds, max 120.")}, ["command"],
         surface=S.SYSTEM, effect=command_effect, fn=backend_tools.run_command, verb="run",
         describe=lambda a: f"Running {_short(a.get('command'), 32)}")
register("list_processes", "Running programs, largest memory users first. Optional name filter.",
         {"name_filter": STR(), "limit": INT()},
         surface=S.SYSTEM, effect=E.OBSERVE, fn=backend_tools.list_processes, verb="look",
         describe=lambda a: "Checking running programs")
register("system_info", "CPU, memory, disk, battery and uptime.",
         surface=S.SYSTEM, effect=E.OBSERVE, fn=backend_tools.system_info, verb="look",
         describe=lambda a: "Checking the system")
register("end_process", "Stop a process (force=true kills it). Use close_window for an ordinary window.",
         {"pid": INT(), "force": BOOL()}, ["pid"],
         surface=S.SYSTEM, effect=E.DESTROY, fn=backend_tools.end_process, verb="run",
         describe=lambda a: f"Stopping process {a.get('pid')}")
register("list_background_jobs", "Programs using the most CPU right now.",
         surface=S.SCREEN, effect=E.OBSERVE, fn=dc.list_background_jobs)
register("get_idle_seconds", "How long since the person last touched the mouse or keyboard.",
         surface=S.SCREEN, effect=E.OBSERVE, fn=idle_behavior.get_idle_seconds)

# ---- time -------------------------------------------------------------------------
_WHEN = {"minutes_from_now": NUM(), "at_time": STR("24-hour HH:MM."),
         "repeat": STR("For a repeating schedule: hourly, daily, weekdays or weekly (weekly repeats on the "
                       "day of the first run). Needs at_time, except hourly.", enum=list(scheduler.REPEATS))}
register("schedule_reminder", "Remind the person later (spoken, bubble and notification), once or on a "
         "repeat. Give minutes_from_now OR at_time.", {"message": STR(), **_WHEN}, ["message"],
         surface=S.TIME, effect=E.INTERACT, fn=scheduler.schedule_reminder, verb="write")
register("schedule_task", "Run an instruction later exactly as if the person had just asked it, once or on a "
         "repeat ('every weekday at 9 give me my briefing'). Give minutes_from_now OR at_time.",
         {"instruction": STR(), **_WHEN}, ["instruction"],
         surface=S.TIME, effect=E.INTERACT, fn=scheduler.schedule_task, verb="write")
register("list_scheduled", "Pending reminders and scheduled tasks, with repeats and any that are paused.",
         surface=S.TIME, effect=E.OBSERVE, fn=scheduler.list_scheduled)
register("resume_scheduled", "Start a paused repeating schedule again (it pauses itself after repeated "
         "failures). Missed runs are not replayed.", {"schedule_id": STR()}, ["schedule_id"],
         surface=S.TIME, effect=E.MODIFY, fn=scheduler.resume_scheduled, verb="write")
register("cancel_scheduled", "Cancel a reminder or scheduled task by its schedule_id.",
         {"schedule_id": STR()}, ["schedule_id"],
         surface=S.TIME, effect=E.MODIFY, fn=scheduler.cancel_scheduled, verb="write")
