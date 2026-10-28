"""Eva core -- which tools the model is shown: a core set always, the rest in groups she opens herself.

Eva 2.0 design, "Immediate gains" (2026-10-28): every model call carried all 136 tool schemas,
about 59,000 characters (some 15,000 tokens). On a free model that is the largest part of each
request, it slows every step, and a long list makes a mid-size model pick the wrong tool more often.

Progressive disclosure, as with skills: the CORE tools (look, read, click, type, keys, windows, apps,
files, shell, browser, memory, skills, the step-by-step operator) go with every call; the others sit in
named GROUPS. The prompt carries a one-line index of the groups with the tool names in each, and
use_tools(groups) opens a group for this turn and the next few. Which group to open is her own
reasoning from the index -- there are no keyword rules.

Nothing is hidden from the permission gate or from dispatch: a tool is only left out of the list the
model sees. If she calls a tool from a closed group (it is named in the index), it runs as usual and its
group opens. Two live facts open a group by themselves, because they are state, not guesses: a game in
progress opens "games", the person's own browser being connected opens "web_pages".

A tool not listed below (one added later, or a connected service's tool) is treated as core, so a new
tool is never silently invisible. config.TOOL_GROUPS = False sends every tool, as before.
"""
import threading

CORE = (
    # see and read
    "look_at_screen", "read_window_text", "read_screen_elements", "list_windows", "get_foreground_app",
    # act on screen
    "focus_window", "launch_app", "close_window", "click_element", "type_into_element", "type_text",
    "send_hotkey", "scroll", "click_coords", "operate_computer", "wait_for_person", "media_control",
    # web
    "browser_open", "browser_act", "read_web_page",
    # files and shell
    "find_files", "list_folder", "read_file", "write_file", "move_file", "create_folder", "run_command",
    "undo_last_change",
    # memory, skills, time
    "remember", "recall", "forget", "find_skill", "load_skill", "read_skill_file", "save_skill",
    "get_current_time", "notify", "schedule_reminder", "stop_activity",
    "use_tools",
)

GROUPS = {
    "windows": ("arrange windows: minimise, maximise, move, resize, saved layouts",
                ("minimize_window", "maximize_window", "restore_window", "move_window", "resize_window",
                 "get_window_rect", "save_layout", "restore_layout", "list_layouts", "restore_foreground")),
    "screen_detail": ("finer screen control: find things by picture, the control tree, right/double click, drag, "
                      "mouse, clipboard, selected text",
                      ("locate_on_screen", "ui_tree", "ui_navigate", "act_on_element", "double_click_coords",
                       "right_click_coords", "drag_and_drop", "move_mouse", "screenshot", "get_selected_text",
                       "get_clipboard", "set_clipboard", "get_screen_size", "recover_input", "get_idle_seconds")),
    "web_pages": ("the person's own browser tabs and pages, page navigation, extracting data, feeds, YouTube "
                  "transcripts",
                  ("page_tabs", "page_elements", "page_read", "page_click", "page_type", "page_go", "page_scroll",
                   "page_activate", "navigate_browser", "switch_browser_tab", "browser_extract", "browser_resume",
                   "read_feed", "youtube_transcript")),
    "files_more": ("more file work: search inside files, duplicates, largest files, recent changes, PDF and Word "
                   "conversion",
                   ("search_files", "duplicate_files", "largest_files", "list_changes", "merge_pdfs", "split_pdf",
                    "pdf_tables_to_csv", "word_to_pdf")),
    "system": ("the PC itself: processes, system info, battery, disk, startup apps, Windows settings, "
               "administrator, Eva's own health",
               ("list_processes", "end_process", "system_info", "battery_status", "disk_space", "startup_apps",
                "list_settings", "change_setting", "open_settings", "restart_as_administrator", "eva_health")),
    "schedule": ("later and repeating work: scheduled tasks, routines, reacting when something happens, focus "
                 "sessions, background jobs",
                 ("schedule_task", "list_scheduled", "cancel_scheduled", "resume_scheduled", "start_repeating",
                  "react_when", "my_routines", "forget_routine", "list_background_jobs", "focus_start", "focus_end")),
    "games": ("playing games with the person",
              ("play_game", "start_game", "end_game", "game_rules", "game_note", "save_game_rules")),
    "body": ("your avatar's body: animations, sitting, walking, going home, what you are doing",
             ("play_animation", "stop_animation", "sit_down", "stand_up", "walk_to_screen", "go_home",
              "sit_on_window", "activity_status", "adjust_activity")),
    "helpers": ("helpers that work in the background for you",
                ("delegate", "agent_message", "helpers_status", "cancel_helper")),
    "recordings": ("teach-and-repeat recordings of the person's steps",
                   ("start_recording", "stop_recording", "list_recordings", "run_recording")),
    "office": ("email drafts and calendar events", ("draft_email", "add_calendar_event")),
    "self": ("your own past: failed tasks and their replays, forgetting everything",
             ("failed_tasks", "show_failed_task", "forget_all")),
}

KEEP_TURNS = 3          # a group opened stays open for this many further turns

_GROUP_OF = {t: g for g, (_d, tools) in GROUPS.items() for t in tools}


def group_of(tool_name):
    return _GROUP_OF.get(tool_name)


def index(registered=None) -> str:
    """The prompt's index of groups: name -- purpose (tool names). Only groups with a registered tool."""
    lines = []
    for g, (purpose, tools) in GROUPS.items():
        names = [t for t in tools if registered is None or t in registered]
        if names:
            lines.append(f"- {g}: {purpose} ({', '.join(names)})")
    if not lines:
        return ""
    return ("MORE TOOLS, IN GROUPS. Your tool list holds the everyday tools. These groups hold the rest; when a "
            "task needs one, call use_tools with the group name(s) first, then use its tools. Decide from what "
            "the task needs, not from words in the request. The core tools often suffice.\n" + "\n".join(lines))


class Toolbox:
    """Per agent: which groups are open, and the schemas to send."""

    def __init__(self, keep_turns=KEEP_TURNS):
        self.keep_turns = keep_turns
        self._open = {}            # group -> turns left
        self._lock = threading.Lock()

    # ---- opening and closing ---------------------------------------------------------------
    def use(self, groups):
        """use_tools: opens the named groups. Returns the tool result."""
        if isinstance(groups, str):
            groups = [g.strip() for g in groups.replace(";", ",").split(",")]
        wanted = [str(g).strip().lower().replace(" ", "_").replace("-", "_") for g in (groups or []) if str(g).strip()]
        known = [g for g in wanted if g in GROUPS]
        unknown = [g for g in wanted if g not in GROUPS]
        with self._lock:
            for g in known:
                self._open[g] = self.keep_turns
        out = {"ok": bool(known), "opened": known,
               "tools": sorted(t for g in known for t in GROUPS[g][1])}
        if unknown:
            out["error" if not known else "note"] = (f"no group called {', '.join(unknown)}; the groups are "
                                                     f"{', '.join(GROUPS)}")
        return out

    def noticed(self, tool_name):
        """She called a tool from a closed group (named in the index): open its group."""
        g = group_of(tool_name)
        if g:
            with self._lock:
                self._open[g] = self.keep_turns

    def opened(self):
        with self._lock:
            return set(self._open)

    def new_turn(self, state=()):
        """At the start of a turn: age the open groups, then open what live state needs."""
        with self._lock:
            for g in list(self._open):
                self._open[g] -= 1
                if self._open[g] < 0:
                    del self._open[g]
            for g in state:
                if g in GROUPS:
                    self._open[g] = max(self._open.get(g, 0), 0)

    # ---- what to send ----------------------------------------------------------------------
    def schemas(self, tools):
        """tools: registry tools (with .name, .schema(), .fn). Core + open groups + anything ungrouped."""
        opened = self.opened()
        out = []
        for t in tools:
            if t.fn is None:
                continue
            g = group_of(t.name)
            if g is None or g in opened:
                out.append(t.schema())
        return out
