"""Plays Eva's continuous-activity controllers against the local test games
(real screen capture, real keyboard and mouse) and prints how each went.

    python tools/play_lab/lab.py [tictactoe connect4 2048 flappy runner scroll]

Needs a desktop (on Windows: just run it; it opens Chromium full screen) and
Playwright's Chromium (playwright install chromium).
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

import activities  # noqa: E402
import games  # noqa: E402
import loops  # noqa: E402
import reflex  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PAGES = {"tictactoe": "tictactoe.html", "connect4": "connect4.html", "2048": "g2048.html",
         "flappy": "flappy.html", "runner": "runner.html"}


def region_of(page, selector):
    r = page.evaluate(f"(() => {{ const b = document.querySelector('{selector}').getBoundingClientRect();"
                      " return [b.left, b.top, b.right, b.bottom]; })()")
    ox = page.evaluate("window.screenX + (window.outerWidth - window.innerWidth) / 2")
    oy = page.evaluate("window.screenY + (window.outerHeight - window.innerHeight)")
    return (int(r[0] + ox), int(r[1] + oy), int(r[2] + ox), int(r[3] + oy))


def viewport_of(page):
    """The visible part of the page on screen (a long page's body runs far below it)."""
    ox = page.evaluate("window.screenX + (window.outerWidth - window.innerWidth) / 2")
    oy = page.evaluate("window.screenY + (window.outerHeight - window.innerHeight)")
    w, h = page.evaluate("[window.innerWidth, window.innerHeight]")
    return (int(ox), int(oy), int(ox + w), int(oy + h))


def play(name, browser):
    page = browser.new_page(viewport=None)
    if name == "scroll":
        page.set_content("<body style='margin:0'>" + "".join(
            f"<p style='font:28px Arial;margin:30px'>Line {i} " + "lorem ipsum " * 8 + "</p>" for i in range(120))
            + "<p id=end style='font:40px Arial'>THE END</p></body>")
    else:
        page.goto("file://" + os.path.join(HERE, PAGES[name]))
    page.bring_to_front()
    time.sleep(1.2)
    full = viewport_of(page) if name == "scroll" else region_of(page, "body")
    if name == "tictactoe":
        act = games.GameActivity("tictactoe", full, first=True, reply_timeout=10)
    elif name == "connect4":
        act = games.GameActivity("connect_four", region_of(page, "canvas"), first=True, reply_timeout=10)
    elif name == "2048":
        act = games.GameActivity("2048", region_of(page, "#b"), max_seconds=240)
    elif name == "flappy":
        act = reflex.FlappyActivity(region_of(page, "canvas"), max_seconds=90)
    elif name == "runner":
        act = reflex.RunnerActivity(region_of(page, "canvas"), max_seconds=90)
    else:
        act = loops.LoopActivity({"kind": "scroll", "direction": "down", "amount": 5}, every=0.15, until="end",
                                 max_seconds=120, rect_of=lambda w: full, window=None)
        act.window = None
        act._rect_of = lambda w: full
        act.until_rect = full
    done = {}
    m = activities.Manager(on_end=lambda a: done.setdefault("a", a))
    if name == "scroll":                         # the loop watches its window; here, the page area
        act.window = "lab"
        m._foreground = lambda: "lab"
    t0 = time.time()
    m.start(act)
    while "a" not in done and time.time() - t0 < act.max_seconds + 20:
        time.sleep(0.5)
    page_result = page.evaluate("window.RESULT || ''") if name != "scroll" else \
        page.evaluate("Math.round(window.scrollY) + '/' + Math.round(document.body.scrollHeight - innerHeight)")
    extra = page.evaluate("window.SCORE !== undefined ? 'score ' + window.SCORE : ''") if name == "flappy" else ""
    a = done.get("a", act)
    print(f"{name:10s} eva: {a.outcome:8s} {a.result!s:45s} | game says: {page_result} {extra} ({time.time() - t0:.0f} s)",
          flush=True)
    page.close()
    return a, page_result


if __name__ == "__main__":
    which = sys.argv[1:] or ["tictactoe", "connect4", "2048", "flappy", "runner", "scroll"]
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, args=["--start-fullscreen", "--window-position=0,0",
                                                     "--window-size=1280,800", "--force-device-scale-factor=1"])
        for n in which:
            play(n, b)
        b.close()
