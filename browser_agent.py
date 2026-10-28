"""
Eva -- real-world internet actions: shopping, booking, messaging other
agents/bots. Wired end to end: browser_act and browser_extract run a real
Playwright + the same NVIDIA-hosted model loop -- no external paid automation service,
just the same model already powering Eva's brain.

Threading note: Playwright's sync API is NOT safe to call from more than
one thread -- a browser/page object is bound to whichever thread created
it. But main.py spawns a fresh background thread per chat/voice message,
so two different messages that both touch the browser would otherwise run
on two different threads and break. Every actual Playwright call is routed
through a single dedicated worker thread (_executor, max_workers=1) so it's
always the same thread underneath, regardless of which thread called in.
"""
import time
import random
import os
import json
import concurrent.futures
import threading
import socket
import ipaddress
from urllib.parse import urlparse
import requests

from logger import get_logger

log = get_logger(__name__)
from playwright.sync_api import sync_playwright

import config
import finance_guard
import system_guard
import permissions

def _complete(messages, temperature):
    """One plain completion on whichever model of the chain is answering now (providers.py)."""
    import providers
    from core import llm
    ref = llm.last_model()
    providers.pace(ref)
    return providers.client(ref, timeout=90.0).chat.completions.create(
        model=providers.parse(ref)[1], messages=providers.clean_messages(ref, messages), temperature=temperature)

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

_pw = None
_browser = None
_page = None


# Set by main.py: on_screen_action(x, y) -- called (synchronously, blocking)
# just before a real click/type lands on the page, so the avatar can
# physically walk to that spot on screen and gesture before the action
# fires, the same as native desktop clicks. Best-effort by design: any
# exception in the callback is swallowed and the browser action proceeds
# -- the action must never fail just because the walk couldn't happen.
on_screen_action = None


def _screen_point_of(page, vx: int, vy: int):
    """Approximate SCREEN coordinates of a viewport point (vx, vy).

    window.screenX/screenY give the outer browser window's OS position;
    outer-vs-inner size differences give the browser chrome (borders,
    tab strip, bookmarks bar) sitting between the window edge and the
    viewport. The left/right chrome is split evenly between the two
    borders, the rest is all at the top. Approximate on DPI-scaled
    displays (devicePixelRatio != 1) -- Eva only needs to stand NEAR the
    element, not pixel-exact. Returns None if the page can't be queried,
    in which case the caller simply skips the walk."""
    try:
        info = page.evaluate(
            "([vx, vy]) => {"
            " const dx = Math.max(0, (window.outerWidth - window.innerWidth) / 2);"
            " const dy = Math.max(0, window.outerHeight - window.innerHeight - dx);"
            " return [window.screenX + dx + vx, window.screenY + dy + vy];"
            "}", [vx, vy])
        return int(info[0]), int(info[1])
    except Exception:
        return None


def _walk_to_point(page, vx: int, vy: int):
    """Fire the physical-presence callback for a viewport point, if one is
    installed and the point resolves to real screen coordinates."""
    if on_screen_action is None:
        return
    try:
        pt = _screen_point_of(page, vx, vy)
        if pt:
            on_screen_action(pt[0], pt[1])
    except Exception:
        pass  # best-effort only -- see on_screen_action's docstring


# Set by main.py when the person says "stop". Browser work is the longest
# thing Eva does -- eva.log 2026-09-26 shows browser_extract holding the
# single worker thread for 110 seconds, during which nothing was heard and
# the person clicked the avatar ten times. stop_requested only unwound at
# tool-ROUND boundaries, and browser_act is one tool call, so it was never
# consulted for the whole duration. Checked between steps here instead.
_abort = threading.Event()

# --- Hooks main.py fills in ---------------------------------------------------
# approval_hook(ApprovalRequest) -> Verdict: asked before a step that spends
#   money. If nothing is wired in, such steps are refused, never assumed.
# on_handoff(info) / on_resumed(info): tell the person a site wants a human,
#   and tell Eva when the person has dealt with it so she can carry on.
approval_hook = None
on_handoff = None
on_resumed = None

HANDOFF_POLL_SECONDS = 3.0        # how often to check whether the person is done
HANDOFF_MAX_SECONDS = 600         # then stop waiting and say so
_handoff = {"info": None}
_handoff_lock = threading.Lock()



def request_abort():
    """Ask any in-flight browser work to stop at its next step boundary. A
    stop also drops a pending human-verification hand-off: after "stop", the
    page clearing later must not bring the old task back."""
    _abort.set()
    _clear_handoff()


def clear_abort():
    _abort.clear()


def aborted() -> bool:
    return _abort.is_set()


_browser_kind = {"k": ""}


def _ensure_browser(browser: str = ""):
    """browser: "" = Eva's bundled Chromium (or whatever is already open);
    otherwise the name of an installed Chromium-based browser ("brave",
    "chrome", "edge"). Asking for a different one than is running closes the
    current one and starts the requested one, with its own profile folder so
    two browsers never share (and corrupt) one profile."""
    global _pw, _browser, _page
    want = (browser or "").strip().lower()
    if _page is not None and want and want != _browser_kind["k"]:
        _close_browser_impl()
    if _page is not None:
        # Probe before trusting the existing page: if the person closed the
        # browser window manually (confirmed live -- a session ended with
        # every subsequent browser call failing TargetClosedError forever,
        # because this only ever checked _page is None), a stale _page
        # object outlives the window. One cheap round-trip detects it.
        try:
            _page.title()
            return _page
        except Exception:
            # Dead browser -- tear the old Playwright objects down cleanly
            # (best-effort; they're broken anyway) and relaunch fresh below.
            try:
                _browser.close()
            except Exception:
                pass
            try:
                _pw.stop()
            except Exception:
                pass
            _browser = _pw = _page = None
    _pw = sync_playwright().start()

    # A PERSISTENT profile, not a fresh ephemeral one per launch.
    # launch() gives Chromium a blank temporary profile every single time,
    # so every session arrives with no cookies, no history and no stored
    # consent -- which is both inconvenient (logins and cookie banners
    # every time) and, to a site, indistinguishable from a machine that
    # has never visited before. eva.log 2026-09-26 hit challenge pages on
    # two different sites within one minute of opening the browser.
    #
    # Also sets a real viewport, locale and timezone. Playwright's
    # defaults (1280x720, no locale) are not wrong exactly, but they are
    # not what this person's normal browser looks like either, and the
    # small viewport changes which layout sites serve.
    exe_path = None
    if want:
        import app_resolver
        found = app_resolver.resolve({"edge": "msedge"}.get(want, want))
        if found.get("kind") == "path" and found["target"].lower().endswith(".exe"):
            exe_path = found["target"]
        else:
            log.warning(f"Browser {want!r} not found as an installed program -- using the default one.")
            want = ""
    _browser_kind["k"] = want
    profile_dir = os.path.join(os.path.expanduser("~"), ".eva",
                               f"browser-profile-{want}" if want else "browser-profile")
    os.makedirs(profile_dir, exist_ok=True)
    _mark_profile_clean(profile_dir)
    width, height = config.BROWSER_VIEWPORT
    try:
        _browser = _pw.chromium.launch_persistent_context(
            profile_dir,
            executable_path=exe_path,
            headless=False,
            viewport={"width": width, "height": height},
            locale=config.BROWSER_LOCALE,
            timezone_id=config.BROWSER_TIMEZONE,
            user_agent=config.BROWSER_USER_AGENT or None,
            args=["--start-maximized", "--hide-crash-restore-bubble", "--disable-session-crashed-bubble"],
        )
        _page = _fresh_page(_browser)
        log.info(f"Browser started with persistent profile at {profile_dir}"
                 + (f" using {exe_path}" if exe_path else ""))
    except Exception as e:
        # A profile left locked by a crashed Chromium would otherwise mean
        # no browser at all. Fall back to the old ephemeral launch and say
        # so, rather than failing the whole request.
        log.warning(f"Persistent profile unavailable ({e}) -- falling back to a temporary one.")
        _browser = _pw.chromium.launch(headless=False)
        _page = _browser.new_page()
    return _page


def _mark_profile_clean(profile_dir):
    """A browser closed by a crash or a kill leaves its profile marked
    'Crashed', and the next start offers -- or performs -- a restore of the
    old tabs. That is how an earlier session's search page came back on its
    own (2026-10-11: yesterday's Kolkata tour search). Mark it clean first."""
    path = os.path.join(profile_dir, "Default", "Preferences")
    try:
        with open(path, encoding="utf-8") as f:
            prefs = json.load(f)
    except (OSError, ValueError):
        return
    prof = prefs.setdefault("profile", {})
    if prof.get("exit_type") == "Normal" and prof.get("exited_cleanly") is True:
        return
    prof["exit_type"], prof["exited_cleanly"] = "Normal", True
    session = prefs.setdefault("session", {})
    if session.get("restore_on_startup") == 1:          # "continue where you left off"
        session["restore_on_startup"] = 5               # open the new-tab page
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
    except OSError:
        pass


def _fresh_page(browser):
    """One blank tab: whatever a restored session brought back is closed, so
    no page from an earlier task is ever read as if it were the current one."""
    pages = list(browser.pages)
    keep = pages[0] if pages else browser.new_page()
    for extra in pages[1:]:
        try:
            extra.close()
        except Exception:
            pass
    try:
        if keep.url not in ("about:blank", ""):
            log.info(f"Browser opened with an old page ({keep.url[:80]}) -- starting blank instead.")
            keep.goto("about:blank")
    except Exception:
        pass
    return keep


def _interactive_elements(page, limit: int = 60):
    """Collects visible, interactable elements with a stable index the model
    can reference, plus enough text to reason about which one is right."""
    els = page.query_selector_all(
        "a, button, input, select, textarea, [role=button], [role=link], [onclick]"
    )
    out = []
    for i, el in enumerate(els):
        if i >= limit:
            break
        try:
            if not el.is_visible():
                continue
            box = el.bounding_box()
            if not box:
                continue
            text = (el.inner_text() or "").strip()[:80]
            placeholder = el.get_attribute("placeholder") or ""
            aria = el.get_attribute("aria-label") or ""
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            # Not shown to the model (they would only cost tokens); read by
            # the payment guard to recognise card and bank-detail fields.
            attrs = " ".join(filter(None, (el.get_attribute(a) for a in
                             ("name", "id", "autocomplete", "aria-label", "placeholder"))))
            out.append({
                "index": len(out),
                "tag": tag,
                "text": text or placeholder or aria,
                "x": int(box["x"] + box["width"] / 2),
                "y": int(box["y"] + box["height"] / 2),
                "type": (el.get_attribute("type") or "").lower(),
                "attrs": attrs,
            })
        except Exception:
            continue
    return out


_BROWSER_SYSTEM_PROMPT = """You control a web browser on behalf of a desktop assistant. You are given
the page title and a list of visible, interactable elements (index, tag,
text/label) -- pick exactly one action, the single best next step toward
the instruction. Never reference an element index that isn't in the given
list. Use "done" as soon as the instruction is already satisfied by the
current page state, or if it genuinely can't be completed from what's on
this page -- don't keep guessing at actions once nothing productive is
left to try. Respond with ONLY a JSON object, no other text, no markdown
code fences, in one of the exact shapes given in the user message."""


def _extract_json(text: str) -> dict:
    """The previous approach -- text[text.find("{"):text.rfind("}")+1] --
    breaks on markdown-fenced JSON (```json ... ```), on any JSON value
    that legitimately contains a '}' character before the real closing
    brace, and returns garbage on an empty/non-JSON response. This tries
    a direct parse first (the common case, already valid JSON), then
    strips markdown fences, then falls back to matching the first
    balanced {...} span by tracking actual brace depth rather than just
    hunting for the first/last brace characters."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return {"action": "done", "reason": "model returned unparseable output"}


def _wait_for_navigation(page):
    """A fixed 500ms wait after a click can't tell whether the click
    triggered a page navigation -- if it did, elements collected before
    the click are stale by the time the next step tries to use them. This
    waits for the DOM to actually be ready, silently passing if the click
    didn't cause a navigation at all (the common case)."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass


def _ask_model_for_action(instruction: str, elements: list, page_title: str, done=()) -> dict:
    history = "\n".join(f"- {d}" for d in list(done)[-8:]) or "- (nothing yet)"
    prompt = f"""Page title: "{page_title}".
Instruction: "{instruction}"

Already done in this instruction (each with whether the page changed):
{history}
If a step did NOT change the page, do something DIFFERENT next (the element may need typing, a
suggestion from a list, scrolling, or a pop-up closing) -- never repeat it.

Visible interactive elements (index, tag, text/label):
{json.dumps([{k: e[k] for k in ("index", "tag", "text")} for e in elements], indent=0)}

Respond with ONLY a JSON object, no other text, in one of these shapes:
{{"action": "click", "index": <int>}}
{{"action": "type", "index": <int>, "text": "<string>"}}
{{"action": "press", "key": "<e.g. Enter>"}}
{{"action": "scroll", "direction": "down" | "up"}}
{{"action": "done", "reason": "<why the instruction is already satisfied>"}}
Pick exactly one action -- the single best next step toward the instruction."""
    resp = _complete([{"role": "system", "content": _BROWSER_SYSTEM_PROMPT},
                      {"role": "user", "content": prompt}], temperature=0.2)
    return _extract_json(resp.choices[0].message.content)


# ---------------------------------------------------------------------------
# Human-verification pages: hand over, wait, and carry on afterwards.
#
# eva.log 2026-09-26: five minutes and ~30 calls spent re-reading the same
# challenge page. She cannot and should not work through one -- but stopping
# dead left the person to notice, tell her, and re-explain the task. Now she
# brings the browser forward, says so once, and WATCHES the page: the moment
# it stops being a challenge she picks the task back up, with the
# conversation history intact. The person does the human part; nothing else.
# ---------------------------------------------------------------------------
def _page_is_challenge(page) -> bool:
    try:
        text = page.inner_text("body")[:2000]
    except Exception:
        text = ""
    return looks_like_challenge(getattr(page, "url", "") or "", text)


def _safe_hook(hook, *args):
    if hook is None:
        return
    try:
        hook(*args)
    except Exception:
        log.exception("A browser hook failed -- continuing")


def _handoff_active():
    with _handoff_lock:
        return _handoff["info"]


def _clear_handoff():
    with _handoff_lock:
        info, _handoff["info"] = _handoff["info"], None
    return info


def _handoff_result(info) -> dict:
    return {"ok": False, "needs_person": True, "url": info["url"],
            "error": "this page is a human-verification check. I can't do that part and "
                     "shouldn't. I've brought the browser forward and told the person; I'll "
                     "carry on by myself when it clears, or you can call browser_resume. Do "
                     "not retry this page -- say so once, or offer a different source."}


def _enter_handoff(page) -> dict:
    existing = _handoff_active()
    if existing:
        return _handoff_result(existing)
    info = {"url": page.url, "title": _safe_title(page), "since": time.time()}
    with _handoff_lock:
        _handoff["info"] = info
    log.info(f"Human-verification page at {info['url']!r} -- handing over to the person.")
    try:
        page.bring_to_front()
    except Exception:
        pass
    _safe_hook(on_handoff, dict(info))
    _schedule_poll()
    return _handoff_result(info)


def _safe_title(page) -> str:
    try:
        return page.title()
    except Exception:
        return ""


def _schedule_poll():
    timer = threading.Timer(HANDOFF_POLL_SECONDS, lambda: _executor.submit(_poll_handoff))
    timer.daemon = True
    timer.start()


def _poll_handoff():
    """Runs on the browser thread (Playwright is not thread-safe)."""
    info = _handoff_active()
    if info is None:
        return
    page = _page
    if page is None:
        _clear_handoff()
        return
    if not _page_is_challenge(page):
        _clear_handoff()
        log.info("The verification page has cleared -- resuming.")
        _safe_hook(on_resumed, {"url": page.url, "title": _safe_title(page)})
        return
    if time.time() - info["since"] > HANDOFF_MAX_SECONDS:
        _clear_handoff()
        log.info("Gave up waiting for the person to clear the verification page.")
        return
    _schedule_poll()


def _resume_impl():
    info = _handoff_active()
    page = _ensure_browser()
    if info is None:
        return {"ok": True, "url": page.url, "note": "nothing was waiting on the person"}
    if _page_is_challenge(page):
        return {"ok": False, "needs_person": True, "url": page.url,
                "error": "the page is still showing the verification check"}
    _clear_handoff()
    return {"ok": True, "resumed": True, "url": page.url, "title": _safe_title(page)}


def _blocked_by_handoff(page):
    """A page that is still a challenge blocks further work on it."""
    info = _handoff_active()
    if info is None:
        return None
    if _page_is_challenge(page):
        return _handoff_result(info)
    _clear_handoff()                      # the person cleared it between our polls
    return None


# ---------------------------------------------------------------------------
# Money: judged per STEP, not per tool.
#
# browser_act used to be confirm-gated wholesale, so "search for flights" and
# "scroll down" asked exactly as loudly as "place order". The person's rule is
# to ask only where money is involved, so each click / keystroke is checked
# against what it actually is (finance_guard) and only those ask -- with a
# question built from the real button label and the real page, not from
# anything the model said.
# ---------------------------------------------------------------------------
def _destructive_check(page, action, el):
    """A click on 'Delete account', 'Remove', 'Sign out'... asks too (system_guard)."""
    if action != "click":
        return None
    label = ((el or {}).get("text") or "").strip()
    reason = system_guard.control_reason(label)
    if not reason:
        return None
    parsed = urlparse(getattr(page, "url", "") or "")
    req = permissions.ApprovalRequest("browser_act", "DESTROY", "This can't be undone",
                                      f"Click “{label[:60]}”\non {parsed.netloc}{parsed.path[:60]}",
                                      f"Because {reason}.", False, "browser_act")
    hook = approval_hook
    if hook is None:
        log.warning("A destructive step needs approval but nobody can be asked -- refused")
        return {"ok": False, "denied": True, "error": permissions.DENIED_MESSAGE}
    verdict = hook(req)
    if not getattr(verdict, "allowed", verdict):
        return {"ok": False, "denied": True, "error": permissions.DENIED_MESSAGE}
    return None


def _financial_check(page, action, el, decision):
    typed = decision.get("text") if action == "type" else decision.get("key") if action == "press" else None
    url, title = getattr(page, "url", "") or "", _safe_title(page)
    reason = finance_guard.browser_step_reason(action, el or {}, url, title, typed)
    if not reason:
        return _destructive_check(page, action, el)
    try:
        total = finance_guard.extract_total(page.inner_text("body")[:6000])
    except Exception:
        total = None
    parsed = urlparse(url)
    label = ((el or {}).get("text") or decision.get("key") or action).strip()
    detail = (f"{action.capitalize()} “{label[:60]}”\non {parsed.netloc}{parsed.path[:60]}"
              + (f"\nThe page shows a total of {total}" if total else ""))
    req = permissions.ApprovalRequest("browser_act", "FINANCIAL", "This involves money", detail,
                                      f"Because {reason}.", False, "browser_act")
    hook = approval_hook
    if hook is None:
        log.warning("A payment step needs approval but nobody can be asked -- refused")
        return {"ok": False, "denied": True, "error": permissions.DENIED_MESSAGE}
    verdict = hook(req)
    if not getattr(verdict, "allowed", verdict):
        return {"ok": False, "denied": True, "error": permissions.DENIED_MESSAGE}
    return None



def _browser_open_impl(url: str, browser: str = ""):
    page = _ensure_browser(browser)
    waiting = _handoff_active()
    if waiting and urlparse(url).netloc and urlparse(url).netloc != urlparse(waiting["url"]).netloc:
        _clear_handoff()             # she is choosing a different source; stop waiting on the old one
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as e:
        # 21:47 on the PC: the site redirected to itself while loading ("interrupted by another
        # navigation") -- three failed opens of a page that was in fact open. That is a page loading.
        if "interrupted by another navigation" not in str(e):
            raise
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
    # Best-effort extra wait for JS-heavy pages (SPAs, React/Vue apps)
    # where domcontentloaded fires before interactive elements have
    # actually rendered. Silently gives up after 3s rather than blocking
    # indefinitely -- some pages hold persistent connections (websockets,
    # ad trackers) that mean networkidle is never reached at all.
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass
    if _page_is_challenge(page):
        return _enter_handoff(page)
    return {"ok": True, "title": page.title()}


def _browser_act_impl(instruction: str, max_steps: int = 6):
    """Runs a real element-grounded action loop until the instruction is
    satisfied or max_steps is hit. Every step that would spend money is
    checked (see _financial_check) and asks the person first; the rest just
    run. A human-verification page ends the loop and hands over."""
    page = _ensure_browser()
    blocked = _blocked_by_handoff(page)
    if blocked:
        return blocked
    steps_taken, done, last, repeats = [], [], None, 0
    for _ in range(max_steps):
        if _abort.is_set():
            log.info("Browser action aborted between steps at the person's request.")
            return {"ok": False, "aborted": True, "steps": steps_taken,
                    "error": "stopped partway through at the person's request"}
        if steps_taken:
            # Playwright fires actions instantly and at perfectly even
            # intervals. A small random pause is both more natural and
            # practically useful: JS-heavy pages were being clicked again
            # before they had finished reacting to the previous step.
            time.sleep(random.uniform(*config.BROWSER_STEP_DELAY))
        elements = _interactive_elements(page)
        before = _page_state(page, elements)
        decision = _ask_model_for_action(instruction, elements, page.title(), done)
        action = decision.get("action")
        steps_taken.append(decision)
        # eva.log 2026-10-17 16:10-16:12 (MakeMyTrip): the same element clicked six times, never
        # noticing nothing happened, then "ok". The same action twice on an unchanged page ends it.
        key = (action, decision.get("index"), decision.get("text"), decision.get("key"))
        repeats = repeats + 1 if key == last else 1
        last = key
        if action != "done" and repeats >= 3:
            return {"ok": False, "steps": steps_taken, "error": f"the same {action} did nothing three times -- "
                    "the page needs something else (a pop-up, a suggestion list, a different control); "
                    "use operate_computer, which looks at the page between steps"}

        if action == "done":
            return {"ok": True, "steps": steps_taken, "reason": decision.get("reason")}

        el = None
        if action in ("click", "type"):
            el = next((e for e in elements if e["index"] == decision.get("index")), None)
            if not el:
                return {"ok": False, "error": "model referenced unknown index", "steps": steps_taken}
        if action in ("click", "type", "press"):
            stop = _financial_check(page, action, el, decision)
            if stop:
                return {**stop, "steps": steps_taken}

        if action == "click":
            _walk_to_point(page, el["x"], el["y"])
            page.mouse.click(el["x"], el["y"])
            _wait_for_navigation(page)
            page.wait_for_timeout(500)
            if _page_is_challenge(page):
                return {**_enter_handoff(page), "steps": steps_taken}

        elif action == "type":
            _walk_to_point(page, el["x"], el["y"])
            page.mouse.click(el["x"], el["y"])
            page.keyboard.type(decision["text"], delay=15)

        elif action == "press":
            page.keyboard.press(decision["key"])
            if _page_is_challenge(page):
                return {**_enter_handoff(page), "steps": steps_taken}

        elif action == "scroll":
            dy = 600 if decision.get("direction") == "down" else -600
            page.mouse.wheel(0, dy)
            page.wait_for_timeout(300)

        else:
            return {"ok": False, "error": f"unrecognized action {action}", "steps": steps_taken}
        changed = _page_state(page) != before
        label = (el or {}).get("text") or decision.get("key") or decision.get("direction") or ""
        done.append(f"{action} {str(label)[:50]!r}" + ("" if changed else " -- the page did NOT change"))

    # Not "ok": nobody checked that the instruction was achieved (it was, wrongly, ok: true).
    return {"ok": False, "steps": steps_taken, "error": f"not finished after {max_steps} steps -- check the "
            "page (browser_extract) before saying it is done, or use operate_computer"}


def _page_state(page, elements=None):
    """A cheap fingerprint of what the page shows: URL, title, the interactive elements' texts."""
    try:
        els = elements if elements is not None else _interactive_elements(page)
        return (getattr(page, "url", ""), page.title(),
                tuple((e.get("tag"), e.get("text")) for e in els[:120]))
    except Exception:
        return None


# eva.log 2026-09-26: Eva spent five minutes and ~30 API calls on a
# Skyscanner PerimeterX challenge and a Google "unusual traffic" page,
# re-extracting the same challenge text over and over. Detecting it once
# and saying so is both more useful and more honest than retrying.
_CHALLENGE_MARKERS = (
    "are you a person or a robot", "press and hold", "unusual traffic",
    "verify you are human", "i'm not a robot", "prove you're not a robot",
    "complete the security check", "/sorry/index", "captcha",
)


def looks_like_challenge(url: str, text: str) -> bool:
    haystack = f"{url}\n{text[:2000]}".lower()
    return any(marker in haystack for marker in _CHALLENGE_MARKERS)


def _browser_extract_impl(what: str):
    if _abort.is_set():
        return {"ok": False, "aborted": True,
                "error": "stopped before extracting, at the person's request"}
    page = _ensure_browser()
    text = page.inner_text("body")[:6000]
    # eva.log 2026-09-30: the browser had been restarted (about:blank) and
    # this returned ok:True with the question echoed back as the "answer".
    # An empty page is a failure the model must be told about, not a result.
    if page.url in ("about:blank", "") or not text.strip():
        return {"ok": False,
                "error": "Eva's own browser has no page open (it shows a blank page). "
                         "Use browser_open with the address first. If the person asked for a "
                         "specific browser, pass browser='brave' (or chrome/edge). navigate_browser "
                         "drives a different browser window that browser_extract cannot read."}
    # #13: everything a page says reaches the model from here, and page
    # text is the one input Eva cannot trust -- a page can carry
    # instructions aimed at her. Logged in full so a human can audit what
    # was actually ingested.
    log.info(f"browser_extract ingesting from {page.url!r}: {text[:1000]!r}")
    if looks_like_challenge(page.url, text):
        return _enter_handoff(page)
    prompt = f"""Page title: "{page.title()}"
Page text:
{text}

Question: {what}
Answer concisely, using only what's actually on the page. If it's not there, say so."""
    resp = _complete([{"role": "user", "content": prompt}], temperature=0.1)
    return {"answer": (resp.choices[0].message.content or "").strip()}


def _close_browser_impl():
    global _browser, _pw, _page
    # Each step independently, so a browser that already died cannot stop
    # Playwright itself being shut down -- leaving _pw running is what
    # orphans the node driver process.
    errors = []
    for label, step in (("browser", lambda: _browser and _browser.close()),
                        ("playwright", lambda: _pw and _pw.stop())):
        try:
            step()
        except Exception as e:
            errors.append(f"{label}: {e}")
    _browser = _pw = _page = None
    if errors:
        log.warning(f"Browser teardown had problems: {'; '.join(errors)}")
        return {"ok": True, "warnings": errors}
    return {"ok": True}


# ---- public API: every call is submitted to the single dedicated browser
# thread and blocks for the result, so callers don't need to know or care
# about the threading constraint above. ----

def browser_open(url: str, browser: str = ""):
    """browser: optional -- "brave", "chrome" or "edge" to drive that installed
    browser instead of the bundled one."""
    return _executor.submit(_browser_open_impl, url, browser).result()


def browser_act(instruction: str, max_steps: int = 6):
    return _executor.submit(_browser_act_impl, instruction, max_steps).result()


def browser_resume():
    """After the person has dealt with a verification page: check, and carry on."""
    return _executor.submit(_resume_impl).result()


def browser_extract(what: str):
    return _executor.submit(_browser_extract_impl, what).result()


# Hosts agent_message may never reach, whatever the model is told by a web
# page. Without this the tool is a straightforward SSRF primitive: it can
# POST arbitrary JSON to anything the machine can route to, including
# services bound to localhost that assume any local caller is trusted. The
# confirm() gate helps, but its summary is written by the model and a
# person may well accept it without reading closely.
def _endpoint_allowed(endpoint: str):
    try:
        parsed = urlparse(endpoint)
    except Exception:
        return False, "not a valid URL"
    if parsed.scheme not in config.AGENT_MESSAGE_ALLOWED_SCHEMES:
        return False, f"scheme {parsed.scheme!r} is not allowed (https only by default)"
    host = parsed.hostname or ""
    if host.lower() in ("localhost", "localhost.localdomain") or host.endswith(".localhost"):
        return False, "refusing to post to localhost"
    try:
        for *_, sockaddr in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(sockaddr[0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast):
                return False, f"refusing to post to a private/internal address ({ip})"
    except Exception as e:
        return False, f"could not resolve {host!r}: {e}"
    return True, None


def agent_message(endpoint: str, payload: dict):
    # Plain HTTP via requests -- not Playwright, no thread affinity issue.
    allowed, why = _endpoint_allowed(endpoint)
    if not allowed:
        log.warning(f"agent_message BLOCKED for {endpoint!r}: {why}")
        return {"ok": False, "error": f"refusing to send to {endpoint}: {why}"}
    log.info(f"agent_message POST -> {endpoint!r}")
    resp = requests.post(endpoint, json=payload, timeout=10)
    resp.raise_for_status()
    body = resp.text[:config.AGENT_MESSAGE_MAX_RESPONSE_CHARS]
    try:
        return {"ok": True, "response": json.loads(body)}
    except ValueError:
        return {"ok": True, "response_text": body}


def close_browser():
    return _executor.submit(_close_browser_impl).result()
