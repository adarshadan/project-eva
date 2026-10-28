"""Desktop capabilities: windows, native UI elements, raw input, the screen,
and teach-and-repeat. Declared once each; see core/registry.py."""
import os

import desktop_control as dc
import recorder
import ui_navigate
import vision
from core.effects import Effect as E, Surface as S, element_effect, hotkey_effect
from core.registry import BOOL, HANDLE, INT, STR, register


def _short(text, n=40) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _base(path) -> str:
    return os.path.basename(str(path or "").rstrip("\\/")) or str(path or "")


_WHY = STR("Why no element from ui_tree could be used (required for raw input).")

# ---- windows -------------------------------------------------------------------
register("list_windows", "The person's open windows with their handles. Eva's own windows are "
         "never listed and must never be closed, minimised or moved.",
         surface=S.WINDOW, effect=E.OBSERVE, fn=dc.list_windows, deadline=20, verb="look",
         describe=lambda a: "Looking at open windows")
register("get_foreground_app", "The window the person is working in right now (title, handle, process).",
         surface=S.WINDOW, effect=E.OBSERVE, fn=dc.get_foreground_app, deadline=15, verb="look")
register("get_window_rect", "Screen rectangle (left/top/right/bottom) of a window.",
         {"window_handle": HANDLE}, ["window_handle"],
         surface=S.WINDOW, effect=E.OBSERVE, fn=dc.get_window_rect, deadline=15)
register("get_screen_size", "Screen and monitor sizes in pixels, plus the usable area. Use it to work "
         "out where a side or corner is instead of guessing coordinates.",
         surface=S.SCREEN, effect=E.OBSERVE, fn=dc.get_screen_size)
register("focus_window", "Bring a window to the front.", {"window_handle": HANDLE}, ["window_handle"],
         surface=S.WINDOW, effect=E.INTERACT, fn=dc.focus_window, deadline=20,
         verb="reach", locus="window", approach="walk")
register("move_window", "Move a window so its top-left corner is at x, y (screen pixels).",
         {"window_handle": HANDLE, "x": INT(), "y": INT()}, ["window_handle", "x", "y"],
         surface=S.WINDOW, effect=E.INTERACT, fn=dc.move_window, deadline=15,
         verb="arrange", locus="coords", approach="walk")
register("resize_window", "Resize a window to w by h pixels.",
         {"window_handle": HANDLE, "w": INT(), "h": INT()}, ["window_handle", "w", "h"],
         surface=S.WINDOW, effect=E.INTERACT, fn=dc.resize_window, deadline=15,
         verb="arrange", locus="window", approach="walk")
register("minimize_window", "Minimise a window.", {"window_handle": HANDLE}, ["window_handle"],
         surface=S.WINDOW, effect=E.INTERACT, fn=dc.minimize_window, deadline=15,
         verb="tidy", locus="caption_min", approach="walk")
register("maximize_window", "Maximise a window.", {"window_handle": HANDLE}, ["window_handle"],
         surface=S.WINDOW, effect=E.INTERACT, fn=dc.maximize_window, deadline=15,
         verb="arrange", locus="caption_max", approach="walk")
register("restore_window", "Restore a minimised or maximised window to its normal size.",
         {"window_handle": HANDLE}, ["window_handle"],
         surface=S.WINDOW, effect=E.INTERACT, fn=dc.restore_window, deadline=15,
         verb="greet", locus="window", approach="walk")
register("restore_foreground", "Give keyboard focus back to a window after working elsewhere.",
         {"window_handle": HANDLE}, ["window_handle"],
         surface=S.WINDOW, effect=E.INTERACT, fn=dc.restore_foreground, deadline=15, embodied=False)
# WM_CLOSE asks the app to close; an app with unsaved work answers with its
# own "save changes?" prompt, so this is not classed as destructive (the
# person's rule). Change the effect to E.DESTROY to make every close ask.
register("close_window", "Close one window by handle. Never use send_hotkey('alt+f4') for this -- that "
         "goes to whatever is in front. If the app asks about unsaved changes the result says "
         "closed=false: look at the screen before saying it closed.",
         {"window_handle": HANDLE}, ["window_handle"],
         surface=S.WINDOW, effect=E.MODIFY, fn=dc.close_window, deadline=15,
         verb="close", locus="caption_close", approach="walk", describe=lambda a: "Closing a window")
register("launch_app", "Start an installed program by its plain name ('notepad', 'spotify', 'brave') "
         "or a full path; it is found the way the Start menu finds it, so do not guess paths. Windows "
         "Settings pages open by name too ('settings', 'windows update', 'bluetooth', 'display settings') "
         "or by address ('ms-settings:windowsupdate'). argument is usually a file to open with it. A file "
         "path alone opens it in its default program.",
         {"name_or_path": STR(), "argument": STR()}, ["name_or_path"],
         surface=S.SYSTEM, effect=E.INTERACT, fn=dc.launch_app, deadline=40, verb="open",
         describe=lambda a: f"Opening {_short(_base(a.get('name_or_path')))}")
register("navigate_browser", "Show a URL in a browser the person ALREADY has open, in a NEW TAB (their "
         "open page is kept); new_tab=false only when they ask to change the current page. You cannot read "
         "that browser's page: to read the same page yourself use read_web_page with the same URL. Never "
         "follow it with browser_extract, which reads YOUR browser.",
         {"window_handle": HANDLE, "url": STR(), "new_tab": BOOL("Default true.")}, ["window_handle", "url"],
         surface=S.SCREEN, effect=E.INTERACT, fn=dc.navigate_browser, deadline=40, verb="open")
register("switch_browser_tab", "Switch to one of the tabs of a browser the person has open, by words in the "
         "tab's title ('MSN', 'YouTube', 'DeepSeek') or by position (index 1 = first, -1 = last, -2 = second "
         "to last). It reads the real tab strip, so 'the last tab' is index -1. Use this instead of "
         "clicking tabs or pressing Ctrl+Tab yourself. On failure it lists every tab title.",
         {"window_handle": HANDLE, "title_contains": STR(), "index": INT()}, ["window_handle"],
         surface=S.SCREEN, effect=E.INTERACT, fn=dc.switch_browser_tab, deadline=40, verb="open",
         describe=lambda a: f"Switching to the {_short(a.get('title_contains') or 'other', 20)} tab")

register("media_control", "Play/pause, skip or mute whatever is playing -- a YouTube tab, a Picture-in-Picture "
         "video, Spotify, VLC -- with the keyboard's media keys, no button to find. Also the system volume: "
         "volume_up / volume_down (times = steps of 2 %). Use this for 'play', 'pause', 'next song', 'louder', "
         "'mute' instead of looking for a button.",
         {"action": STR(enum=["play_pause", "next", "previous", "stop", "mute", "volume_up", "volume_down"]),
          "times": INT("How many presses (volume steps). Default 1.")}, ["action"],
         surface=S.SYSTEM, effect=E.INTERACT, fn=dc.media_key, verb="none",
         describe=lambda a: {"play_pause": "Play / pause", "next": "Next track", "previous": "Previous track",
                             "mute": "Mute", "volume_up": "Louder", "volume_down": "Quieter"}.get(a.get("action"),
                                                                                                  "Media key"))

# ---- native UI elements ----------------------------------------------------------
register("ui_tree", "Read a window's controls from its accessibility tree: names, types, element ids "
         "and exact positions. Fast. Blind to canvas, games and custom-drawn controls -- look at the "
         "screen for those. May include 'remembered' hints: what worked in this app before. Lists visible, "
         "named controls first; pass contains='words' to list only controls whose name holds those words "
         "(the quickest way to find one control in a big window).",
         {"window_handle": HANDLE, "contains": STR("Optional: only controls whose name contains these words.")},
         ["window_handle"],
         surface=S.SCREEN, effect=E.OBSERVE, fn=dc.ui_tree, verb="read", locus="window", approach="gaze",
         describe=lambda a: "Reading the window")
register("read_window_text", "All readable text in a window. Use it to check that typing really landed.",
         {"window_handle": HANDLE}, ["window_handle"],
         surface=S.SCREEN, effect=E.OBSERVE, fn=dc.read_window_text, deadline=30,
         verb="read", locus="window", approach="gaze", describe=lambda a: "Reading the window")
register("click_element", "Click a control found by ui_tree (uses its native Invoke where it can).",
         {"window_handle": HANDLE, "element_id": STR()}, ["window_handle", "element_id"],
         surface=S.SCREEN, effect=E.INTERACT, fn=dc.click_element, deadline=30,
         verb="reach", locus="element", approach="walk", recordable=True, learnable=True,
         record_step=lambda a: {"type": "click_element", **a})
register("type_into_element", "Set the text of a field found by ui_tree. mode: replace (clears it first), "
         "append (adds to the end), at_cursor. Choose deliberately for a document that already has text.",
         {"window_handle": HANDLE, "element_id": STR(), "text": STR(),
          "mode": STR(enum=["replace", "append", "at_cursor"])},
         ["window_handle", "element_id", "text"],
         surface=S.SCREEN, effect=E.INTERACT, fn=dc.type_into_element, deadline=60,
         verb="type", locus="element", approach="walk", recordable=True, learnable=True,
         describe=lambda a: "Typing",
         record_step=lambda a: {"type": "type_into_element", "window_handle": a["window_handle"],
                                "element_id": a["element_id"], "text": a["text"]})
register("act_on_element", "Act on a control by what it IS: toggle, expand, collapse, select, set_value "
         "(text or slider number), focus, scroll_into_view, get_state, or auto. The result says whether "
         "its state really changed. Prefer this to click_element for checkboxes, tabs, lists and sliders.",
         {"window_handle": HANDLE, "element_id": STR(),
          "action": STR(enum=["auto", "invoke", "toggle", "expand", "collapse", "select",
                              "set_value", "focus", "scroll_into_view", "get_state"]),
          "value": STR("For set_value only.")},
         ["window_handle", "element_id"],
         surface=S.SCREEN, effect=element_effect, fn=dc.act_on_element, deadline=45,
         verb="reach", locus="element", approach="walk", learnable=True,
         describe=lambda a: str(a.get("action", "auto")).replace("_", " ").capitalize())
register("ui_navigate", "Reach a control by whatever works when the direct route failed or you have no "
         "element id: its UI pattern, its label, Tab-navigating to it, then looking at the screen and "
         "clicking it. Stops as soon as something acted, so it never repeats an action.",
         {"window_handle": HANDLE, "target": STR("The control's visible label, as a person reads it."),
          "action": STR(enum=["click", "toggle", "select", "expand", "collapse", "set_value", "focus", "type"]),
          "value": STR(), "element_id": STR("Optional known id from ui_tree, tried first.")},
         ["window_handle", "target"],
         surface=S.SCREEN, effect=E.INTERACT, fn=ui_navigate.navigate, deadline=120,
         verb="reach", locus="window", approach="gaze", learnable=True,
         describe=lambda a: f"Finding {_short(a.get('target'))}")

# ---- operating the computer like a person (core/operator.py) ----------------------------
register("operate_computer", "Do a MULTI-STEP job on the screen in any app or web page the way a person "
         "does: look, act, check, repeat -- each control numbered on a screenshot, every step checked, every "
         "press through the usual approvals. Use it for anything that takes more than two or three clicks "
         "or fields (a booking or search form, a settings page, an unfamiliar app, a site in the person's own "
         "browser), and whenever single clicks or browser_act did not get there. Put EVERYTHING the person "
         "asked into goal (what, where, dates, how many, which site) and what 'finished' looks like. It "
         "returns what it achieved, or exactly what stopped it (a login, an OTP, a choice -- it asks the "
         "person and waits). Do not call it for one click, or to read something (locate_on_screen, "
         "look_at_screen).",
         {"goal": STR("The whole job in plain words, with every detail the person gave and what done looks "
                      "like, e.g. 'On makemytrip.com in the open browser tab: search one-way flights Kolkata "
                      "(CCU) to Ho Chi Minh City (SGN) on 25 December 2026 for 2 adults, and show the results; "
                      "do not book.'"),
          "max_steps": INT("Most actions to take (default 25).")}, ["goal"],
         surface=S.SCREEN, effect=E.INTERACT, fn=None, embodied=False, verb="reach", locus="foreground",
         describe=lambda a: f"Working on screen: {_short(a.get('goal'), 60)}")
register("wait_for_person", "Pause and wait for the person to do something themselves -- type a phone "
         "number, an OTP or a password, sign in, solve a human check, make a choice -- or to answer a "
         "question. Use it as soon as they say they will do it ('let me enter the number', 'I'll give the "
         "OTP'). It says `say` aloud, then waits until they speak or type again (up to `minutes`), and "
         "returns what they said. Never type passwords or OTPs yourself.",
         {"say": STR("What you are waiting for, said to the person ('Go ahead and enter the OTP -- say "
                     "continue when it's done.')"), "minutes": INT("How long to wait (default 5, at most 15).")},
         ["say"], surface=S.SELF, effect=E.OBSERVE, fn=None, embodied=False,
         describe=lambda a: "Waiting for you")

# ---- raw input -----------------------------------------------------------------------
for _name, _fn, _verb, _desc in (
        ("click_coords", dc.click_coords, "Click", "Left-click at a screen position."),
        ("right_click_coords", dc.right_click_coords, "Right-click", "Right-click at a screen position (context menu)."),
        ("double_click_coords", dc.double_click_coords, "Double-click", "Double-click at a screen position.")):
    register(_name, _desc + " Only when ui_tree has no usable element. screen_changed=false in the result "
             "means it probably missed.",
             {"x": INT(), "y": INT(), "reason_no_element": _WHY}, ["x", "y", "reason_no_element"],
             surface=S.SCREEN, effect=E.INTERACT, fn=_fn, deadline=20,
             verb="reach", locus="coords", approach="walk", recordable=True,
             record_step=(lambda n: (lambda a: {"type": n, "x": a["x"], "y": a["y"]}))(_name))
register("drag_and_drop", "Press at one point, drag to another and release.",
         {"from_x": INT(), "from_y": INT(), "to_x": INT(), "to_y": INT(), "reason_no_element": _WHY},
         ["from_x", "from_y", "to_x", "to_y", "reason_no_element"],
         surface=S.SCREEN, effect=E.INTERACT, fn=dc.drag_and_drop, deadline=30,
         verb="reach", locus="coords", approach="walk", recordable=True,
         record_step=lambda a: {"type": "drag", "from_x": a["from_x"], "from_y": a["from_y"],
                                "to_x": a["to_x"], "to_y": a["to_y"]})
register("scroll", "Scroll the mouse wheel: positive up, negative down; optionally at x, y.",
         {"amount": INT(), "x": INT(), "y": INT()}, ["amount"],
         surface=S.SCREEN, effect=E.INTERACT, fn=dc.scroll, deadline=20,
         verb="scroll", locus="coords", approach="gaze")
register("move_mouse", "Move the cursor without clicking (hover a menu or tooltip).",
         {"x": INT(), "y": INT()}, ["x", "y"],
         surface=S.SCREEN, effect=E.INTERACT, fn=dc.move_mouse, deadline=15,
         verb="point", locus="coords", approach="walk")
register("type_text", "Type text into a window. Always pass window_handle so the typing can be verified.",
         {"text": STR(), "window_handle": HANDLE}, ["text"],
         surface=S.SCREEN, effect=E.INTERACT, fn=dc.type_text, deadline=60,
         verb="type", locus="foreground", approach="gaze", recordable=True, describe=lambda a: "Typing",
         record_step=lambda a: {"type": "type_text", "text": a["text"]})
register("send_hotkey", "Press a key combination such as 'ctrl+s' or 'win+d'. Pass window_handle to "
         "bring that window forward first so the keys cannot land elsewhere.",
         {"keys": STR(), "window_handle": HANDLE}, ["keys"],
         surface=S.SCREEN, effect=hotkey_effect, fn=dc.send_hotkey, deadline=20,
         verb="type", locus="foreground", approach="gaze", recordable=True,
         record_step=lambda a: {"type": "hotkey", "keys": a["keys"]})
register("recover_input", "Release any stuck mouse buttons or modifier keys.",
         surface=S.SCREEN, effect=E.INTERACT, fn=dc.recover_input, deadline=10)

# ---- seeing -------------------------------------------------------------------------
register("screenshot", "Capture the screen to a file.",
         surface=S.SCREEN, effect=E.OBSERVE, fn=dc.screenshot, deadline=20, verb="look",
         describe=lambda a: "Looking at the screen")
register("look_at_screen", "Actually look at the screen with a vision model and answer a question about "
         "what is visible. Slower than ui_tree but sees everything, including canvas and games. If the "
         "person says something is on screen and the tree does not show it, look.",
         {"question": STR(), "left": INT("Optional box, screen pixels: only look at this area."),
          "top": INT(), "right": INT(), "bottom": INT()}, ["question"],
         surface=S.SCREEN, effect=E.OBSERVE, fn=vision.look_at_screen, deadline=90, verb="look", web=True,
         describe=lambda a: "Looking at the screen")
register("locate_on_screen", "THE way to find something on screen in ANY window -- browser pages, desktop apps, "
         "the desktop's icons, the taskbar. Returns screen coordinates (center_x, center_y) for click_coords. It "
         "reads, in order: the window's own structure (via='window structure': the control's real name, role and "
         "rectangle, also every element of a web page), the words on screen (via='text on screen'), then a "
         "vision model. Describe text targets by the WORDS they show ('Accept all', "
         "the video's title); pictures and icons by what they look like ('man holding a deer'). Narrow the "
         "search when the person says where: area=near_me for 'above your head' / 'next to you', "
         "right_half / left_half / top_half / bottom_half, or an exact box. refined=false means a coarse guess.",
         {"description": STR(), "area": STR("Where to look. Default anywhere.", enum=vision.AREAS),
          "left": INT("Optional box to search, screen pixels."), "top": INT(), "right": INT(), "bottom": INT()},
         ["description"],
         surface=S.SCREEN, effect=E.OBSERVE, fn=vision.locate_on_screen, deadline=90, verb="look",
         describe=lambda a: f"Looking for {_short(a.get('description'))}")

# ---- teach and repeat ---------------------------------------------------------------
register("start_recording", "Start watching the person's clicks and keys to learn a task under a name.",
         {"name": STR()}, ["name"], surface=S.SCREEN, effect=E.INTERACT, fn=recorder.start_recording)
register("stop_recording", "Stop and save the current recording.",
         surface=S.SCREEN, effect=E.INTERACT, fn=recorder.stop_recording)
register("list_recordings", "Tasks Eva has been taught.",
         surface=S.SCREEN, effect=E.OBSERVE, fn=recorder.list_recordings)
# Not gated as a whole: each replayed step goes through the same dispatch and
# is judged on its own. Bound at startup to the agent's dispatcher.
register("run_recording", "Repeat a taught task. Every step is judged and performed like a live one.",
         {"name": STR(), "params": {"type": "object"}}, ["name"],
         surface=S.SCREEN, effect=E.INTERACT, verb="work",
         describe=lambda a: f"Replaying {_short(a.get('name'))}")
