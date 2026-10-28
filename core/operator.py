"""Eva core -- operating the computer the way a person does: look, act, check, repeat.

2026-10-17 16:13 (eva.log, MakeMyTrip): asked to look up flights for two people, Eva's browser helper
pressed the same element six times, never looked at the page between presses, and reported success
("max_steps reached", ok: true). The person: "Eva still can't handle any complex task -- reuse your
computer use skill". This is that loop, the one Claude uses to drive a PC:

    1. LOOK    a screenshot of the screen as it is now (her own windows painted out), with every
               control of the window in front -- from its accessibility tree, or the words on
               screen when it publishes none -- drawn as a NUMBERED BOX (set-of-mark). The vision
               model points at a number, so a click lands on the control's real rectangle, not
               on a guessed coordinate.
    2. DECIDE  the vision model sees the goal, what was done so far and what each step changed,
               and answers with ONE action as JSON.
    3. ACT     through the agent's own dispatch: every click and keystroke passes the permission
               gate (money, can't-be-undone, passwords), is traced, and shows on the task card.
    4. CHECK   the next look shows the result; a step that changed nothing is reported to the
               model as such. Three steps in a row that change nothing, or the same press three
               times, stop the loop with the honest reason -- never "done" on a guess.

The person stays in charge: whatever they say while it runs joins the goal at the next step; when
something needs THEM (a login, a phone number, an OTP, a choice) the loop asks and waits for them
(wait_person) instead of guessing; "stop" ends it. Passwords, OTPs and card numbers are never typed.

Pure orchestration: observe / decide / dispatch / wait_person / should_stop are injected, so the
checks drive it with a scripted model and a fake screen.
"""
import json
import re
import time

from logger import get_logger

log = get_logger(__name__)

MAX_STEPS = 25
MAX_MARKS = 70
SETTLE_SECONDS = 0.7
NO_CHANGE_LIMIT = 3
SAME_ACTION_LIMIT = 3
POINTED = ("click", "double_click", "right_click", "type", "scroll")
ACTIONS = ("click", "double_click", "right_click", "type", "key", "scroll", "wait", "zoom", "open_url", "open_app",
           "done", "ask_person", "stuck")


# ---- the marks ----------------------------------------------------------------------------------
INTERACTIVE = {"button", "check box", "combo box", "field", "link", "list item", "menu item", "radio button",
               "tab item", "tree item", "split button", "data item", "header item", "slider", "image"}


def marks_from_elements(elements, screen=None, limit=MAX_MARKS):
    """Accessibility elements -> numbered marks: the interactive ones, on screen, small enough to be
    one control, duplicates (same name, overlapping) once; top-to-bottom, left-to-right."""
    out = []
    sw, sh = screen or (100000, 100000)
    for e in elements or []:
        role = e.get("role", "")
        if role not in INTERACTIVE:
            continue
        l, t, r, b = e.get("rect") or (0, 0, 0, 0)
        if r - l < 4 or b - t < 4 or r <= 0 or b <= 0 or l >= sw or t >= sh:
            continue
        if (r - l) * (b - t) > 0.5 * sw * sh:
            continue
        if not e.get("enabled", True):
            continue
        name = " ".join(str(e.get("name") or "").split())[:60]
        if role == "image" and not name:
            continue
        cx, cy = (l + r) // 2, (t + b) // 2
        if any(m["name"] == name and abs(m["x"] - cx) < 12 and abs(m["y"] - cy) < 12 for m in out):
            continue
        out.append({"role": role, "name": name, "rect": [l, t, r, b], "x": cx, "y": cy})
    out.sort(key=lambda m: (m["rect"][1] // 12, m["rect"][0]))
    for i, m in enumerate(out[:limit], start=1):
        m["n"] = i
    return out[:limit]


def marks_from_words(words, origin=(0, 0), limit=MAX_MARKS):
    """OCR words (text, left, top, width, height, line, ...) in image pixels -> marks per short phrase
    (words on one line that touch), for windows that publish no structure (games, canvases)."""
    lines = {}
    for w in words or []:
        try:
            text, x, y, ww, hh, line = w[0], int(w[1]), int(w[2]), int(w[3]), int(w[4]), w[5]
        except (IndexError, TypeError, ValueError):
            continue
        if not str(text).strip():
            continue
        lines.setdefault(line, []).append((x, y, ww, hh, str(text)))
    out = []
    for line in lines.values():
        line.sort()
        cur = None
        for x, y, ww, hh, text in line:
            if cur and x - cur["r"] <= max(8, hh * 0.8):
                cur["r"], cur["b"] = max(cur["r"], x + ww), max(cur["b"], y + hh)
                cur["t"] = min(cur["t"], y)
                cur["text"] += " " + text
            else:
                if cur:
                    out.append(cur)
                cur = {"l": x, "t": y, "r": x + ww, "b": y + hh, "text": text}
        if cur:
            out.append(cur)
    marks = []
    for p in out:
        l, t, r, b = p["l"] + origin[0], p["t"] + origin[1], p["r"] + origin[0], p["b"] + origin[1]
        marks.append({"role": "text", "name": p["text"][:60], "rect": [l, t, r, b], "x": (l + r) // 2,
                      "y": (t + b) // 2})
    marks.sort(key=lambda m: (m["rect"][1] // 12, m["rect"][0]))
    for i, m in enumerate(marks[:limit], start=1):
        m["n"] = i
    return marks[:limit]


def text_rows(elements, limit=60):
    """Every labelled element as text, ROW BY ROW top to bottom (a game board reads as its rows:
    "1st letter, C, absent | 2nd letter, R, present in another position | ...")."""
    rows = {}
    for e in elements or []:
        name = " ".join(str(e.get("name") or "").split())
        if not name or len(name) > 160:
            continue
        l, t, r, b = e.get("rect") or (0, 0, 0, 0)
        rows.setdefault(round(((t + b) // 2) / 14), []).append((l, name))
    out = []
    for key in sorted(rows):
        seen, cells = set(), []
        for _, name in sorted(rows[key]):
            if name not in seen:
                seen.add(name)
                cells.append(name)
        out.append(" | ".join(cells)[:400])
    return out[:limit]


def draw_marks(img, marks, origin=(0, 0), scale=1.0):
    """The screenshot with each mark's box and number drawn on it (a copy)."""
    from PIL import ImageDraw
    out = img.copy()
    d = ImageDraw.Draw(out)
    for m in marks:
        l, t, r, b = ((m["rect"][0] - origin[0]) * scale, (m["rect"][1] - origin[1]) * scale,
                      (m["rect"][2] - origin[0]) * scale, (m["rect"][3] - origin[1]) * scale)
        d.rectangle((l, t, r, b), outline=(255, 0, 170), width=2)
        label = str(m["n"])
        tw = 7 * len(label) + 6
        lx, ly = max(0, l - 1), max(0, t - 15) if t >= 15 else t
        d.rectangle((lx, ly, lx + tw, ly + 14), fill=(255, 221, 0))
        d.text((lx + 3, ly + 1), label, fill=(0, 0, 0))
    return out


def mark_list(marks, limit=MAX_MARKS):
    return "\n".join(f"[{m['n']}] {m['role']} '{m['name']}'" for m in marks[:limit]) or "(no numbered controls)"


# ---- what the model is asked ---------------------------------------------------------------------
def prompt(goal, history, obs, notes=(), ledger=None, extra=""):
    """SEE, THEN DO (2026-10-18 00:11: "she decides beforehand what she needs to do instead of seeing and
    doing -- that is where the challenge lies"). Every step starts from the screenshot in front of it:
    first what is there now and whether the last step did what it was meant to, then the notes kept so
    far are corrected by what is seen, and only then ONE action, chosen for what is on the screen --
    never the next item of a plan made earlier without looking."""
    ledger = ledger or {}
    w, h = obs.get("dims") or (0, 0)
    done = "\n".join(f"{i}. {s}" for i, s in enumerate(history[-12:], start=max(1, len(history) - 11))) or "(nothing yet)"
    added = ("\nThe person added while you worked:\n" + "\n".join(f"- {n}" for n in notes)) if notes else ""
    facts = "\n".join(f"- {f}" for f in ledger.get("facts") or []) or "(none yet)"
    expect = ledger.get("check") or ""
    texts = "\n".join(obs.get("texts") or [])[:2000]
    return (
        "You are operating a Windows PC for the person, one action at a time, like a careful human: you LOOK, "
        "then you act on what you SEE.\n"
        f"GOAL: {goal}{added}\n\n"
        f"In front: {obs.get('title') or '(unknown window)'}\n"
        f"Steps so far (each with what it changed):\n{done}\n"
        + (f"Your last step was meant to show: {expect}\n" if expect else "")
        + f"Your notes so far (correct them from the screen):\n{facts}\n"
        + (f"Text on screen, row by row:\n{texts}\n" if texts else "")
        + (f"{extra}\n" if extra else "")
        + f"\nThe screenshot is {w}x{h}. Numbered pink boxes mark the controls you can use:\n{mark_list(obs.get('marks') or [])}\n\n"
        "First LOOK: what is on the screen now, and did your last step do what it was meant to? Then decide the "
        "ONE action that this screen calls for.\n"
        "Reply with ONLY one JSON object, no other text:\n"
        '{"see": "<what is on the screen now that matters, and what changed since the last step>", '
        '"last_step_worked": "<yes | no | unsure>", "facts": ["<everything you now know that matters for the '
        'goal -- in a game: every result so far>"], "expect": "<what the screen will show if THIS action works>", '
        '"action": "<one of: click, double_click, '
        'right_click, type, key, scroll, wait, zoom, open_url, open_app, done, ask_person, stuck>", "mark": <number '
        'of the box, for click/type/scroll/zoom>, "box": [x0, y0, x1, y1] (0-1000, for zoom on an area), "point": [x, y] (0-1000 normalised, ONLY if no box fits), "text": '
        '"<for type>", "enter": <true to press Enter after typing>, "keys": "<for key, e.g. ctrl+l, enter, esc, '
        'tab, backspace>", "times": <for key: how many presses, default 1>, "direction": "<up|down, for scroll>", "url": "<for open_url>", "app": "<for open_app, e.g. '
        'notepad>", "say": "<for done: what was achieved; for ask_person: the question; for stuck: why>", '
        '"board": <ONLY in a letter-feedback word game (Wordle and the like): every SCORED row you see, top to '
        'bottom, as [["CRANE", "bbygb"], ...] with g = green/right place, y = yellow/elsewhere, b = grey/not in '
        'the word; [] if none yet>}\n\n'
        "Rules:\n"
        "- FIRST make sure the right app or site is in front. If the goal names a website and no browser shows "
        "it, use open_url (it opens in my browser and comes to the front); if it needs an app that is not open, "
        "open_app. Never click or type in a window that has nothing to do with the goal.\n"
        "- On a wrong page (an error, 404, 'page not found', a different site), go straight to the right "
        "address with open_url instead of clicking around.\n"
        "- zoom when the controls are small or crowded (calendar days, small icons, fine print, a list of "
        "near-identical items) or you cannot read a label: the next screenshot shows only that area, larger, "
        "with its boxes numbered. Then act on the right box.\n"
        "- ONE action per reply. Prefer a numbered box; use point only for something with no box.\n"
        "- Stay on the site the goal names; do not switch to a different site that looks similar.\n"
        "- type clicks the box first (when you give one) and types there; give enter:true to submit.\n"
        "- Enter a word or text with ONE type action, not letter by letter on an on-screen keyboard (live "
        "Wordle: five steps per guess ran out the budget). For a game board with no text box, type with no "
        "box: the keys go to the page. Use on-screen keys only if typing did not reach it. A word game takes the keys as soon as its page is in "
        "front: never click a tile or an empty row to 'focus' it -- just type the word with enter:true.\n"
        "- Look at the result of your last step before the next. If it changed nothing, do something "
        "DIFFERENT (a pop-up, banner or dialog in the way? the wrong field? scroll to it?).\n"
        "- Close cookie/consent banners by declining what is optional. Close ads and pop-ups that block.\n"
        "- NEVER type a password, an OTP, a PIN, a card number or a phone number you were not given: when a "
        "login, OTP, payment detail, captcha or a choice only the person can make is needed, use ask_person "
        "and say exactly what you need them to do.\n"
        "- Never press a button that pays, books, places an order, deletes or sends unless the goal asks "
        "for exactly that; the person is asked to confirm such presses anyway.\n"
        "- done ONLY when the screenshot SHOWS the goal achieved (say what you see that proves it). "
        "stuck when you cannot get further; say why.")


# ---- thinking it over (2026-10-18) ---------------------------------------------------------------------
# The main language model does NOT plan the job ahead without seeing it ("she decides beforehand what she
# needs to do instead of seeing and doing"). It is asked only after a step that did not work, with the
# eyes' fresh report of the screen, why that was and what to try instead -- advice the next look checks.
PLANNER_SYSTEM = (
    "You help someone operating a Windows PC who just tried a step that did not work. You do not see the "
    "screen; you get a fresh report of it. Be concrete and brief. Reply with ONE JSON object and nothing else.")


def parse_json(raw):
    raw = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S)
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if m:
        raw = m.group(1)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_action(raw):
    """The model's reply -> {action, ...} or None."""
    raw = str(raw or "").strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if m:
        raw = m.group(1)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    act = str(data.get("action") or "").strip().lower().replace(" ", "_").replace("-", "_")
    if act not in ACTIONS:
        return None
    data["action"] = act
    return data


# ---- the loop -------------------------------------------------------------------------------------
class Operator:
    def __init__(self, dispatch, observe, decide, *, wait_person=None, should_stop=None, take_notes=None,
                 to_screen=None, sleep=time.sleep, max_steps=MAX_STEPS, plan=None):
        """dispatch(tool, args) -> result     the agent's pipeline (gate, trace, card)
        observe() -> {"b64", "dims", "marks", "title", "fp", "window"}
        decide(prompt_text, obs) -> raw model text
        wait_person(question) -> their words, or None (no answer / stopped)
        should_stop() -> True when the person said stop
        take_notes() -> [text] the person said while it worked
        to_screen(point, obs) -> (x, y) screen pixels for a 0-1000 point"""
        self.dispatch, self.observe, self.decide = dispatch, observe, decide
        self.wait_person = wait_person or (lambda q: None)
        self.should_stop = should_stop or (lambda: False)
        self.take_notes = take_notes or (lambda: [])
        self.to_screen = to_screen or _default_to_screen
        self.sleep, self.max_steps = sleep, max(1, min(int(max_steps or MAX_STEPS), 60))
        self.pinned = None                     # the window the job is being done in
        self._warned_loop = False
        self._zoom = None                      # the area the next look shows, after a zoom
        self.plan = plan                       # plan(prompt, hard) -> raw JSON text; None = one model does all
        self.ledger = {"plan": [], "facts": [], "check": ""}
        self._last_see = ""
        self._board = []
        self._seen_board = []                  # a word game board as the eyes read its colours

    def run(self, goal):
        goal = " ".join(str(goal or "").split())
        history, notes, steps = [], [], []
        unchanged, last_key, repeats = 0, None, 0
        obs = self.observe()
        for step in range(1, self.max_steps + 1):
            if self.should_stop():
                return self._end(False, "stopped: the person said stop", steps, history, notes, stopped=True)
            notes.extend(self.take_notes())
            if not obs or not obs.get("b64"):
                return self._end(False, "I could not see the screen", steps, history, notes)
            text = prompt(goal, history, obs, notes, self.ledger, self._extra(history, obs, goal, notes))
            try:
                raw = self.decide(text, obs)
            except Exception as e:
                # 22:21 on the PC: one 500 from the model service ended a job 33 steps in. Once more,
                # after a pause, before giving up.
                log.info(f"Operator: the vision model failed ({str(e)[:100]}); trying once more")
                self.sleep(3.0)
                try:
                    raw = self.decide(text, obs)
                except Exception as e2:
                    return self._end(False, f"the vision model did not answer ({str(e2)[:120]})", steps, history,
                                     notes)
            act = parse_action(raw)
            if act is None:
                history.append("(my reply could not be read; answer with the JSON object only)")
                log.info(f"Operator: unreadable reply {str(raw)[:160]!r}")
                continue
            kind = act["action"]
            if kind == "click" and str(act.get("text") or "").strip():
                # 22:01 on the PC: "click" with text='Kolkata' four times on a field that was ready for
                # input -- the text says what was meant: type it there.
                act["action"] = kind = "type"
            log.info(f"Operator step {step}: {kind} {_brief(act)} -- sees: {str(act.get('see', ''))[:120]}")
            self._last_see = str(act.get("see") or "")[:400]
            self._keep(act)
            typed = getattr(self, "_typed", "")
            # Once per string: live Wordle 2026-10-28 08:40, the tile labels said "SS" was still typed while
            # the eyes saw GROSS scored (labels lag the flip animation) -- blocking again stalled the game.
            if typed and typed in getattr(self, "_typed_warned", set()):
                typed = ""
            if kind == "type" and typed and str(act.get("text") or "").strip().isalpha():
                self._typed_warned = getattr(self, "_typed_warned", set()) | {typed}
                log.info(f"Operator: the row still holds {typed!r} -- not typing {act.get('text')!r} on top of it")
                history.append(f"you were about to type {str(act.get('text'))!r}, but the active row still holds "
                                f"{typed} (typed, not scored). Delete it first: Backspace {len(typed)} times.")
                continue
            board = self._board or getattr(self, "_seen_board", None) or []
            if kind == "type" and board:
                ok, why = wordplay_check(act.get("text"), board)
                if not ok:
                    log.info(f"Operator: guess {act.get('text')!r} does not fit the board ({why}) -- not typed")
                    history.append(f"you were about to type {str(act.get('text'))!r}, but it does not fit what the "
                                   f"board has shown: {why}. Choose a word that fits ALL of it.")
                    continue
            if kind == "done":
                return self._end(True, act.get("say") or act.get("see") or "done", steps, history, notes)
            if kind == "stuck":
                return self._end(False, act.get("say") or "I could not get further", steps, history, notes)
            if kind == "ask_person":
                q = act.get("say") or "I need you for the next step."
                answer = self.wait_person(q)
                if answer is None:
                    return self._end(False, f"waiting for you: {q}", steps, history, notes, needs_person=q)
                history.append(f"asked the person: {q!r} -- they said: {answer!r}")
                obs = self._look_again()
                continue
            if kind == "type" and _looks_secret(act.get("text", ""), goal, notes):
                history.append("refused to type what looks like an OTP/PIN that the person did not give -- "
                               "use ask_person so they type it themselves")
                continue
            if kind in POINTED and self.pinned and obs.get("window") and obs.get("window") != self.pinned:
                # 21:47 on the PC: a click meant for the browser landed on File Explorer behind it, and
                # the rest of the job went on in the wrong window. The window being worked in comes back
                # to the front first; the model is told, and looks again.
                r = self.dispatch("focus_window", {"window_handle": self.pinned})
                if r.get("ok", "error" not in r):
                    history.append(f"'{obs.get('title') or 'another window'}' had come in front of the window you "
                                   "were working in; I brought yours back -- look again before acting")
                    obs = self._look_again()
                    continue
                self.pinned = None                     # it is gone: work with what is in front now
            key = (kind, _mark_number(act.get("mark")), str(act.get("text") or act.get("keys") or act.get("url") or
                                                          act.get("app") or ""), tuple(act.get("point") or ()))
            repeats = repeats + 1 if key == last_key else 1
            last_key = key
            if repeats > SAME_ACTION_LIMIT:
                return self._end(False, f"the same {kind} did nothing {SAME_ACTION_LIMIT} times", steps, history,
                                 notes)
            done_text, result = self._act(act, obs)
            steps.append({"action": kind, "what": done_text, "ok": bool(result.get("ok", "error" not in result))})
            if kind in ("open_url", "open_app"):
                self.pinned = None                     # a new place to work: pinned at its first action
            elif kind in POINTED and obs.get("window") and not result.get("outside"):
                self.pinned = self.pinned or obs.get("window")
            recent = [s_["what"] for s_ in steps[-6:]]
            pts = act.get("point") if kind in POINTED else None
            self._points = (getattr(self, "_points", []) + [(kind, pts)])[-6:]
            # live Wordle 2026-10-28: clicks a few pixels apart on an empty row, "to focus it", never repeat
            # exactly -- so clicks near the same spot count as the same thing
            near = sum(1 for k_, p_ in self._points if k_ == kind and _near(p_, pts)) if pts else 0
            if kind in POINTED and (recent.count(done_text) >= 3 or near >= 3):
                # 21:48-21:49: "Flights" pressed again and again (with other presses in between, so never
                # twice in a row) while the page moved a little each time. The first time she is told to
                # change approach; the second time it ends.
                if self._warned_loop:
                    return self._end(False, f"I keep doing the same thing ({done_text}) without getting further",
                                     steps, history, notes)
                self._warned_loop = True
                history.append(f"WARNING: {done_text} has now been done three times and the job is no further. "
                               "Do something DIFFERENT: a field that is ready for input needs the TYPE action with "
                               "its text; or go straight to the right page with open_url, use another control, "
                               "scroll, or close what is in the way.")
            if result.get("denied"):
                history.append(f"{done_text} -- the person declined it")
                return self._end(False, f"you declined: {done_text}", steps, history, notes)
            if result.get("error") and not result.get("ok"):
                history.append(f"{done_text} -- FAILED: {str(result.get('error'))[:120]}")
                obs = self._look_again()
                continue
            before = obs.get("fp")
            obs = self._look_again()
            changed = bool(obs) and obs.get("fp") != before
            if kind in ("wait", "zoom"):
                changed = True
            unchanged = 0 if changed else unchanged + 1
            history.append(f"{done_text} -- " + ("the screen changed" if changed else "the screen did NOT change"))
            if unchanged >= NO_CHANGE_LIMIT:
                return self._end(False, f"{NO_CHANGE_LIMIT} steps in a row changed nothing on screen", steps,
                                 history, notes)
        return self._end(False, f"not finished after {self.max_steps} steps", steps, history, notes)

    # ---- notes kept across steps; thinking harder when a step did not work -------------------------
    def _keep(self, act):
        seen = board_from_report(act.get("board"))
        if seen:
            self._seen_board = seen               # the eyes' own reading, for boards with no text labels
        facts = [str(f)[:200] for f in (act.get("facts") or []) if str(f).strip()][:25]
        if facts:
            self.ledger["facts"] = facts
        self.ledger["check"] = str(act.get("expect") or "")[:200]

    def _extra(self, history, obs, goal, notes):
        """What goes into this look besides the screen: what the board of a word game has established, and
        -- after a step that did not work -- advice from thinking it over (the planner, with the eyes' own
        fresh report of the screen, never a plan made without looking)."""
        from core import wordplay
        self._board = wordplay.parse_board(obs.get("texts") or []) if obs else []
        self._typed = wordplay.pending(obs.get("texts") or []) if obs else ""
        parts = []
        if self._board:
            parts.append("KNOWN FROM THE BOARD (read from its tiles): " + wordplay.summary(self._board))
        elif getattr(self, "_seen_board", None):
            parts.append("KNOWN FROM THE BOARD (as you read its colours last look): "
                         + wordplay.summary(self._seen_board))
        if self._typed:
            parts.append(wordplay.pending_note(self._typed))
        last = history[-1] if history else ""
        stuck = bool(last) and any(k in last for k in ("did NOT change", "FAILED", "WARNING", "does not fit"))
        if self.plan and stuck and self._last_see:
            advice = self._think(goal, history, notes)
            if advice:
                parts.append(f"ADVICE FROM THINKING IT OVER (check it against what you see): {advice}")
        return "\n".join(parts)

    def _think(self, goal, history, notes):
        text = (f"GOAL: {goal}\n" + ("The person added: " + "; ".join(notes) + "\n" if notes else "")
                + "Steps so far:\n" + "\n".join(history[-12:])
                + f"\nWhat is on the screen right now (seen just now): {self._last_see}\n"
                + "Notes: " + "; ".join(self.ledger.get("facts") or []) + "\n"
                + "The last step did not work. In two or three sentences: why, most likely, and what to try "
                  "instead on THIS screen. Reply with JSON: {\"advice\": \"...\"}")
        try:
            data = parse_json(self.plan(text, True)) or {}
        except Exception as e:
            log.info(f"Operator: thinking it over failed ({str(e)[:100]})")
            return ""
        advice = str(data.get("advice") or "")[:500]
        if advice:
            log.info(f"Operator advice: {advice[:200]}")
        return advice

    # ---- one action ----------------------------------------------------------------------------
    def _target(self, act, obs):
        marks = {m["n"]: m for m in obs.get("marks") or []}
        n = _mark_number(act.get("mark"))
        if n in marks:
            m = marks[n]
            return (m["x"], m["y"]), f"[{n}] {m['role']} '{m['name']}'"
        p = act.get("point")
        if isinstance(p, (list, tuple)) and len(p) == 2:
            try:
                x, y = self.to_screen((float(p[0]), float(p[1])), obs)
                return (int(x), int(y)), f"the point {int(x)},{int(y)}"
            except (TypeError, ValueError):
                pass
        return None, ""

    def _act(self, act, obs):
        kind = act["action"]
        where, label = self._target(act, obs)
        window = obs.get("window")
        rect = obs.get("rect")
        if where is not None and rect and kind in POINTED and not (
                rect[0] <= where[0] < rect[2] and rect[1] <= where[1] < rect[3]):
            return (f"{kind} {label}", {"ok": False, "outside": True,
                    "error": f"{where[0]},{where[1]} is outside the window being worked in "
                             f"('{obs.get('title') or ''}'); not pressed -- pick a box inside it"})
        if kind in ("click", "double_click", "right_click"):
            if where is None:
                return f"{kind} (no box or point given)", {"ok": False, "error": "say which box to click"}
            tool = {"click": "click_coords", "double_click": "double_click_coords",
                    "right_click": "right_click_coords"}[kind]
            r = self.dispatch(tool, {"x": where[0], "y": where[1], "reason_no_element": f"operator: {label}"})
            return f"{kind.replace('_', '-')} {label}", r
        if kind == "type":
            text = str(act.get("text") or "")
            if not text:
                return "type (nothing to type)", {"ok": False, "error": "give the text to type"}
            if where is not None:
                r = self.dispatch("click_coords", {"x": where[0], "y": where[1],
                                                   "reason_no_element": f"operator: focus {label}"})
                if r.get("denied") or (r.get("error") and not r.get("ok")):
                    return f"click {label} to type", r
                self.sleep(0.25)
            args = {"text": text}
            if window:
                args["window_handle"] = window
            r = self.dispatch("type_text", args)
            if act.get("enter") and (r.get("ok") or not r.get("error")):
                self.dispatch("send_hotkey", {"keys": "enter", **({"window_handle": window} if window else {})})
            shown = text if len(text) <= 40 else text[:37] + "..."
            return f"type {shown!r}" + (f" into {label}" if label else "") + (" and Enter" if act.get("enter") else ""), r
        if kind == "key":
            keys = str(act.get("keys") or "").strip()
            if not keys:
                return "key (none given)", {"ok": False, "error": "give the keys"}
            try:
                times = max(1, min(20, int(act.get("times") or 1)))
            except (TypeError, ValueError):
                times = 1
            r = {"ok": True}
            for _ in range(times):                 # e.g. Backspace 5 times to clear a rejected word
                r = self.dispatch("send_hotkey", {"keys": keys, **({"window_handle": window} if window else {})})
                if not (isinstance(r, dict) and r.get("ok", True)):
                    break
            return f"press {keys}" + (f" x{times}" if times > 1 else ""), r
        if kind == "scroll":
            amount = 5 if str(act.get("direction") or "down").lower().startswith("u") else -5
            args = {"amount": amount}
            if where is not None:
                args.update(x=where[0], y=where[1])
            r = self.dispatch("scroll", args)
            return f"scroll {'up' if amount > 0 else 'down'}" + (f" at {label}" if label else ""), r
        if kind == "zoom":
            area = None
            marks = {m["n"]: m for m in obs.get("marks") or []}
            n = _mark_number(act.get("mark"))
            if n in marks:
                l, t, r, b_ = marks[n]["rect"]
                area = (l - 160, t - 120, r + 160, b_ + 200)
            elif isinstance(act.get("box"), (list, tuple)) and len(act["box"]) == 4:
                try:
                    x0, y0 = self.to_screen((float(act["box"][0]), float(act["box"][1])), obs)
                    x1, y1 = self.to_screen((float(act["box"][2]), float(act["box"][3])), obs)
                    area = (int(min(x0, x1)), int(min(y0, y1)), int(max(x0, x1)), int(max(y0, y1)))
                except (TypeError, ValueError):
                    area = None
            if not area or area[2] - area[0] < 40 or area[3] - area[1] < 30:
                return "zoom (no area)", {"ok": False, "error": "give a box number or an area to zoom into"}
            self._zoom = tuple(int(v) for v in area)
            return f"zoom into {label or 'an area'}", {"ok": True, "zoom": True}
        if kind == "open_url":
            url = str(act.get("url") or act.get("text") or "").strip()
            if not url:
                return "open_url (no address)", {"ok": False, "error": "give the url"}
            if not re.match(r"https?://", url):
                url = "https://" + url
            r = self.dispatch("browser_open", {"url": url})
            self._to_front(r.get("title") if isinstance(r, dict) else "")
            self.sleep(1.5)
            return f"open {url}", r
        if kind == "open_app":
            app = str(act.get("app") or act.get("text") or "").strip()
            if not app:
                return "open_app (no app)", {"ok": False, "error": "give the app"}
            r = self.dispatch("launch_app", {"name_or_path": app})
            self.sleep(1.5)
            return f"open the app {app}", r
        if kind == "wait":
            secs = 2.0
            try:
                secs = max(0.5, min(float(act.get("seconds") or 2), 6))
            except (TypeError, ValueError):
                pass
            self.sleep(secs)
            return f"wait {secs:.0f} s", {"ok": True}
        return kind, {"ok": False, "error": f"unknown action {kind}"}

    def _to_front(self, title):
        """The browser it just opened, in front: the next look must be at it, not at what was there."""
        words = str(title or "").strip()[:25]
        if not words:
            return
        try:
            wins = (self.dispatch("list_windows", {}) or {}).get("windows") or []
            hit = next((w for w in wins if words.lower() in str(w.get("title", "")).lower()), None)
            if hit:
                self.dispatch("focus_window", {"window_handle": hit["handle"]})
        except Exception:
            log.exception("Operator: could not bring the browser forward")

    def _look_again(self):
        self.sleep(SETTLE_SECONDS)
        crop, self._zoom = self._zoom, None
        try:
            return self.observe(crop) if crop else self.observe()
        except Exception:
            log.exception("Operator: could not look at the screen")
            return None

    def _end(self, ok, said, steps, history, notes, **extra):
        log.info(f"Operator {'finished' if ok else 'stopped'} after {len(steps)} actions: {said[:160]}")
        out = {"ok": ok, ("result" if ok else "error"): said, "actions": len(steps), "steps": history[-12:],
               "verified": ok}
        if self.ledger.get("plan"):
            out["plan"] = self.ledger["plan"]
        if self.ledger.get("facts"):
            out["facts"] = self.ledger["facts"]
        if notes:
            out["the_person_said_meanwhile"] = notes
        out.update(extra)
        return out


_COLOUR = {"g": "correct", "y": "present", "b": "absent", "x": "absent", "-": "absent", ".": "absent"}


def _near(a, b, within=40):
    """Two pointed spots (0-1000) close enough to be the same click."""
    try:
        return bool(a) and bool(b) and abs(float(a[0]) - float(b[0])) <= within and \
            abs(float(a[1]) - float(b[1])) <= within
    except (TypeError, ValueError, IndexError):
        return False


def board_from_report(rows):
    """The eyes' "board": [["CRANE", "bbygb"], ...] -> [(WORD, [state...])], or [] when unusable.
    Live Wordle 2026-10-28 on a site whose tiles have no labels: SLATE was guessed after CRANE came back all
    grey -- nothing checked guesses against the colours."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not (isinstance(row, (list, tuple)) and len(row) == 2):
            continue
        word = re.sub(r"[^A-Za-z]", "", str(row[0])).upper()
        cols = re.sub(r"[^a-z.\-]", "", str(row[1]).lower())
        if len(word) < 4 or len(word) != len(cols) or any(c not in _COLOUR for c in cols):
            continue
        out.append((word, [_COLOUR[c] for c in cols]))
    if out and len({len(w) for w, _ in out}) != 1:
        return []
    return out


def wordplay_check(text, board):
    from core import wordplay
    w = str(text or "").strip()
    if not board or not w.isalpha() or len(w) != len(board[0][0]):
        return True, ""
    return wordplay.check(w, board)


def _mark_number(v):
    """8, "8", [8], "[8]", "#8" -> 8 (models write the box number in all of these; 21:40 on the PC: [8])."""
    if isinstance(v, (list, tuple)):
        v = v[0] if len(v) == 1 else None
    if v is None or isinstance(v, bool):
        return None
    m = re.fullmatch(r"\s*\[?\s*#?(\d{1,4})\s*\]?\s*", str(v))
    return int(m.group(1)) if m else None


def _looks_secret(text, goal, notes):
    """A 4-8 digit code the person never said: an OTP or PIN the model made up or read off a message."""
    t = str(text or "").strip()
    return bool(re.fullmatch(r"\d{4,8}", t)) and t not in (goal + " " + " ".join(notes))


def _brief(act):
    bits = [f"{k}={act[k]!r}" for k in ("mark", "point", "text", "keys", "direction") if act.get(k) not in (None, "")]
    return " ".join(bits)[:120]


def _default_to_screen(point, obs):
    w, h = obs.get("dims") or (1, 1)
    scale = obs.get("scale") or 1.0
    ox, oy = obs.get("origin") or (0, 0)
    x, y = point
    if max(x, y) <= 1.0:
        x, y = x * 1000, y * 1000
    return ox + (x * w / 1000) / scale, oy + (y * h / 1000) / scale


# ---- the real screen (Windows) -----------------------------------------------------------------------
def observe_screen(crop=None):
    """One look: masked screenshot + numbered marks of the window in front. crop (screen pixels): a
    zoom -- only that area, enlarged, with only its marks."""
    import desktop_control as dc
    import structure
    import vision
    img, origin = vision._capture(crop)
    if img is None:
        return None
    fg = {}
    try:
        fg = dc.get_foreground_app() or {}
    except Exception:
        pass
    marks = []
    h = fg.get("handle")
    if not h or dc.is_own_window(int(h)):
        # Her own chat window is in front when the request was typed: the job is in the window
        # behind it (list_windows is front first and leaves her own windows out).
        try:
            top = (dc.list_windows().get("windows") or [{}])[0]
            h, fg = top.get("handle"), {"handle": top.get("handle"), "title": top.get("title")}
        except Exception:
            h = None
    if h and not dc.is_own_window(int(h)):
        try:
            marks = marks_from_elements(structure.snapshot(int(h)), screen=(origin[0] + img.width,
                                                                              origin[1] + img.height))
        except Exception:
            log.exception("Operator: structure unavailable")
    texts = []
    if h and not dc.is_own_window(int(h)):
        try:
            texts = text_rows(structure.snapshot(int(h)))
        except Exception:
            texts = []
    if len([m for m in marks if m["role"] != "image"]) < 6:
        try:
            import screen_text
            words = screen_text.ocr_words(img)
            own = list(vision.SELF_RECTS)
            extra = [m for m in marks_from_words(words, origin)
                     if not any(l <= m["x"] <= r and t <= m["y"] <= b for (l, t, r, b) in own)]
            base = len(marks)
            for i, m in enumerate(extra[:MAX_MARKS - base], start=base + 1):
                m["n"] = i
            marks += extra[:MAX_MARKS - base]
        except Exception:
            log.exception("Operator: OCR unavailable")
    if crop:
        l, t = origin
        r, b_ = l + img.width, t + img.height
        marks = [m for m in marks if l <= m["x"] < r and t <= m["y"] < b_]
    shown = vision._mask_own_windows(img, origin)
    scale = 1.0
    if crop and shown.width < 1100:
        scale = min(2.5, 1100.0 / max(1, shown.width))       # a zoom: larger, so small labels can be read
        shown = shown.resize((int(shown.width * scale), int(shown.height * scale)))
    elif shown.width > vision._MAX_W:
        scale = vision._MAX_W / shown.width
        shown = shown.resize((vision._MAX_W, int(shown.height * scale)))
    marked = draw_marks(shown, marks, origin, scale)
    fp = getattr(vision._tls, "fp", None)
    rect = None
    if h:
        try:
            r = dc.get_window_rect(int(h))
            rect = (r["left"], r["top"], r["right"], r["bottom"]) if r else None
        except Exception:
            rect = None
    return {"b64": vision._encode(marked), "dims": marked.size, "scale": scale, "origin": origin, "marks": marks,
            "title": fg.get("title") or "", "window": int(h) if h else None, "fp": fp, "rect": rect,
            "texts": texts}


def decide_with_vision(prompt_text, obs):
    """2026-10-28 (tools/vision_trial.py, six word-game boards with known answers): the same model read 6/6
    exactly with thinking on and 0/6 with it off (grey rows reported green, typed letters missed). The
    operator acts on what it sees, so it looks with thinking on (config.OPERATOR_VISION_THINKING)."""
    import config
    import vision
    think = bool(getattr(config, "OPERATOR_VISION_THINKING", True))
    return vision._ask(prompt_text, obs["b64"], max_tokens=500, deadline=time.monotonic() + (75 if think else 45),
                       think=think)


def to_screen_vision(point, obs):
    """A point the vision model gave (its own coordinate convention) -> screen pixels."""
    import vision
    w, h = obs["dims"]
    x1, y1, _, _ = vision.to_image(point[0], point[1], point[0], point[1], w, h, vision.convention())
    scale = obs.get("scale") or 1.0
    ox, oy = obs.get("origin") or (0, 0)
    return ox + x1 / scale, oy + y1 / scale
