"""Continuous activities: repeat-until loops, turn-based games, real-time play
(activities.py, loops.py, games.py, reflex.py). Each tool STARTS something
that then runs on its own; the result arrives later and is said aloud."""
import activities as act
import games
import loops
import perception
import reflex
from core.effects import Effect as E, Surface as S
from core.registry import ARR, BOOL, HANDLE, INT, NUM, STR, register


def _area(window_handle=None, left=None, top=None, right=None, bottom=None):
    if None not in (left, top, right, bottom):
        return perception.clip_to_screen((int(left), int(top), int(right), int(bottom)))
    rect = perception.window_client_rect(window_handle) if window_handle else None
    if not rect:
        return None
    return perception.clip_to_screen(rect)


def _focus(window_handle):
    if window_handle:
        import desktop_control as dc
        dc.focus_window(window_handle)


def start_repeating(action, window_handle=None, direction="down", amount=3, keys="", x=None, y=None,
                    seconds=0.5, text="", every=0.5, until="stopped", until_value="", max_minutes=10):
    a = {"kind": action}
    if action == "scroll":
        a.update(direction=direction or "down", amount=amount or 3)
    elif action in ("keys", "hold"):
        if not keys:
            return {"ok": False, "error": "say which keys, e.g. keys='space' or 'ctrl+right'"}
        a.update(keys=keys, seconds=seconds)
    elif action == "click":
        if x is None or y is None:
            return {"ok": False, "error": "a click needs x and y (screen pixels)"}
        a.update(x=x, y=y)
    elif action == "type":
        a.update(text=text)
    why = loops._refused(a)
    if why:
        return {"ok": False, "error": why}
    if until in ("end", "text", "gone") and not window_handle:
        return {"ok": False, "error": f"until={until} needs the window_handle to watch"}
    _focus(window_handle)
    job = loops.LoopActivity(a, every=every, until=until, until_value=until_value or None,
                             window=window_handle, max_seconds=int(float(max_minutes or 10) * 60))
    return act.manager().start(job)


def play_game(game, window_handle=None, left=None, top=None, right=None, bottom=None, i_go_first=None,
              key="", max_minutes=15):
    area = _area(window_handle, left, top, right, bottom)
    if not area:
        return {"ok": False, "error": "give the game's window_handle (or its area: left, top, right, bottom)"}
    _focus(window_handle)
    secs = int(float(max_minutes or 15) * 60)
    if game in games.GameActivity.GAMES:
        job = games.GameActivity(game, area, window=window_handle, first=i_go_first, max_seconds=secs)
    elif game == "flappy_bird":
        job = reflex.FlappyActivity(area, window=window_handle, max_seconds=secs, key=key or "space")
    elif game == "runner":
        job = reflex.RunnerActivity(area, window=window_handle, max_seconds=secs, jump=key or "space")
    else:
        return {"ok": False, "error": f"I cannot play {game!r} yet"}
    return act.manager().start(job)


def react_when(trigger, window_handle=None, left=None, top=None, right=None, bottom=None, colour=None,
               text="", action="space", times=0, cooldown=0.3, max_minutes=10):
    area = _area(window_handle, left, top, right, bottom)
    if not area:
        return {"ok": False, "error": "give the window_handle or the area (left, top, right, bottom) to watch"}
    if trigger == "colour" and not (isinstance(colour, (list, tuple)) and len(colour) == 3):
        return {"ok": False, "error": "colour must be [red, green, blue], 0-255 each"}
    if trigger == "text" and not text:
        return {"ok": False, "error": "say which words to wait for"}
    why = loops._refused({"kind": "keys", "keys": action}) if action != "click" else None
    if why:
        return {"ok": False, "error": why}
    _focus(window_handle)
    job = reflex.ReactActivity(area, trigger=trigger, action=action, colour=colour, text=text, times=times,
                               cooldown=cooldown, window=window_handle, max_seconds=int(float(max_minutes or 10) * 60))
    return act.manager().start(job)


def adjust_activity(faster=False, slower=False, every=None, direction=None, amount=None):
    return act.manager().adjust(faster=faster, slower=slower, every=every, direction=direction, amount=amount)


def stop_activity():
    stopped = act.manager().stop("asked to stop")
    return {"ok": True, "stopped": stopped}


def activity_status():
    return act.manager().state()


_UNTIL = ["stopped", "end", "count", "seconds", "text", "gone"]
register("start_repeating", "Keep doing one thing over and over ON ITS OWN until told to stop or until it is "
         "done: 'keep scrolling until I say stop' (action=scroll), 'scroll to the bottom' (until=end), 'press the "
         "right arrow every 2 seconds', 'click Next 20 times' (until=count), 'until it says Done' (until=text). "
         "It returns at once and runs in the background; the person (or stop_activity) stops it, and how it "
         "ended is said automatically. Use this for anything continuous instead of calling scroll or "
         "send_hotkey again and again. Keys that close or delete are refused.",
         {"action": STR(enum=["scroll", "keys", "hold", "click", "type"]), "window_handle": HANDLE,
          "direction": STR("scroll: up, down, left, right. Default down.", enum=["up", "down", "left", "right"]),
          "amount": INT("scroll: notches each time (1-50). Default 3."),
          "keys": STR("keys / hold: e.g. 'space', 'right', 'ctrl+right'."),
          "x": INT("click: screen x"), "y": INT("click: screen y"),
          "seconds": NUM("hold: how long to hold the keys each time."), "text": STR("type: what to type each time."),
          "every": NUM("Seconds between repeats, 0.05-60. Default 0.5."),
          "until": STR("When to end: stopped (default: until told), end (the window stops changing -- the "
                       "end of a page), count, seconds, text (words appear on screen), gone (words disappear).",
                       enum=_UNTIL),
          "until_value": STR("For count / seconds: the number. For text / gone: the words."),
          "max_minutes": NUM("Safety limit. Default 10.")}, ["action"],
         surface=S.SCREEN, effect=E.INTERACT, fn=start_repeating, verb="scroll", embodied=False,
         describe=lambda a: f"Starting to repeat: {a.get('action')}")
register("play_game", "Play a game on screen BY YOURSELF until it is over: tictactoe, connect_four, 2048 (turn by "
         "turn: the board is read from the screen and every move is computed exactly -- do not choose moves "
         "yourself), flappy_bird and runner (Chrome's dinosaur and similar: played in real time, many decisions "
         "a second). Works in any program or browser: give the window (or the game's area on screen). Returns "
         "at once; the result (won, lost, score) is said automatically when the game ends. For a game against "
         "the person, they make their moves on the same board and she waits for each.",
         {"game": STR(enum=["tictactoe", "connect_four", "2048", "flappy_bird", "runner"]), "window_handle": HANDLE,
          "left": INT(), "top": INT(), "right": INT(), "bottom": INT(),
          "i_go_first": BOOL("Turn-based: true if you move first, false if the opponent does. Default: worked out "
                             "from the board."),
          "key": STR("Real-time: the key that flaps / jumps. Default space."),
          "max_minutes": NUM("Safety limit. Default 15.")}, ["game"],
         surface=S.SCREEN, effect=E.INTERACT, fn=play_game, verb="work", embodied=False,
         describe=lambda a: f"Playing {a.get('game', 'a game')}")
register("react_when", "Watch an area of the screen and react the instant something happens, on your own, many "
         "times a second: when the picture changes (trigger=change), when a colour appears (colour), when words "
         "appear (text) -> press a key or click. For reaction tests, 'press space when it turns green', 'click "
         "whenever the button shows up'. Returns at once; runs until stopped, `times` reactions, or the limit.",
         {"trigger": STR(enum=["change", "colour", "text"]), "window_handle": HANDLE,
          "left": INT(), "top": INT(), "right": INT(), "bottom": INT(),
          "colour": ARR("integer", "colour trigger: [red, green, blue]"), "text": STR("text trigger: the words"),
          "action": STR("A key ('space', 'enter', 'a') or 'click' (the middle of the area). Default space."),
          "times": INT("Stop after this many reactions; 0 = until stopped."),
          "cooldown": NUM("Seconds to wait after each reaction. Default 0.3."),
          "max_minutes": NUM("Safety limit. Default 10.")}, ["trigger"],
         surface=S.SCREEN, effect=E.INTERACT, fn=react_when, verb="look", embodied=False,
         describe=lambda a: "Watching the screen")
register("adjust_activity", "Change the running repeat while it runs: faster, slower, every N seconds, another "
         "scroll direction or amount ('faster', 'go up now').",
         {"faster": BOOL(), "slower": BOOL(), "every": NUM(), "direction": STR(enum=["up", "down", "left", "right"]),
          "amount": INT()}, surface=S.SCREEN, effect=E.INTERACT, fn=adjust_activity, verb="none", embodied=False)
register("stop_activity", "Stop the running repeat, game or reaction.",
         surface=S.SCREEN, effect=E.INTERACT, fn=stop_activity, verb="none", embodied=False)
register("activity_status", "What is running on its own right now (a repeat, a game), how far it got, or how the "
         "last one ended.", surface=S.SCREEN, effect=E.OBSERVE, fn=activity_status, verb="look", embodied=False)
