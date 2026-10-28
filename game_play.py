"""Eva -- playing ANY game by learning it first, then playing it move by move.

2026-10-12: "instead of fixed logic, she can first look up the rules on the internet,
understand it and then try to play -- that would be generic". Before this, Wordle went
wrong three ways (eva.log 2026-10-11 23:58 - 00:06): she looked up the day's answer
(the person: "you should not cheat"); then, playing fairly, she did not know the rules
well enough (typed a second word into a row before pressing Enter); and the board
state she had worked out was trimmed out of the conversation, so she re-derived it,
went round in circles, and lost the thread.

So, for any game:
  1. RULES CARD. game_rules(name): what she already knows about the game. If nothing,
     she looks the rules up (a how-to-play page, never solutions) and saves a short card
     with save_game_rules: the goal; how a move is made on THIS computer (keys, clicks);
     how to read the state on screen; how it ends; a sound basic strategy. Kept in
     ~/.eva/games/, reused next time.
  2. NOTEBOOK. While a game is on (start_game ... end_game), the rules card and her
     notebook (state, moves, what she learned from each result -- game_note) are put in
     front of the model on EVERY step, so trimming the conversation cannot lose them.
  3. FAIR PLAY. While a game is on, looking up answers, solutions or hints is refused,
     unless the person asked for them in so many words.
  4. READING THE BOARD exactly: read_screen_elements (structure.py) lists every labelled
     element of the window, row by row -- a web game's tiles, cells, cards, scores.
"""
import json
import os
import re
import threading
import time

from logger import get_logger

log = get_logger(__name__)

FOLDER = os.path.join(os.path.expanduser("~"), ".eva", "games")
NOTE_LINES = 40
_lock = threading.Lock()
CURRENT = {"game": None, "rules": "", "notes": [], "started": 0.0, "fair": True}


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:60] or "game"


def _path(name):
    return os.path.join(FOLDER, _slug(name) + ".json")


# ---- rules cards ---------------------------------------------------------------------------
def game_rules(name):
    try:
        with open(_path(name), encoding="utf-8") as f:
            card = json.load(f)
        return {"ok": True, "found": True, "game": card.get("name", name), "rules": card.get("rules", ""),
                "saved": card.get("saved", ""), "sources": card.get("sources", [])}
    except (OSError, ValueError):
        return {"ok": True, "found": False, "game": name,
                "how": ("You do not know this game yet. Look up its RULES (search 'how to play <game>' and read "
                        "one good how-to page with read_web_page) -- never answers, solutions or today's puzzle. "
                        "Then save_game_rules with: GOAL; HOW A MOVE IS MADE here (which keys or clicks, and what "
                        "confirms a move); HOW TO READ THE STATE on screen; HOW IT ENDS; a sound BASIC STRATEGY.")}


def save_game_rules(name, rules, sources=None):
    rules = str(rules or "").strip()
    if len(rules) < 40:
        return {"ok": False, "error": "write the rules card properly: goal, how a move is made, how to read the "
                                      "state, how it ends, a basic strategy"}
    os.makedirs(FOLDER, exist_ok=True)
    card = {"name": str(name), "rules": rules[:4000], "saved": time.strftime("%Y-%m-%d %H:%M"),
            "sources": [str(s) for s in (sources or [])][:5]}
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump(card, f, indent=1)
    with _lock:
        if CURRENT["game"] and _slug(CURRENT["game"]) == _slug(name):
            CURRENT["rules"] = card["rules"]
    return {"ok": True, "saved": card["name"], "note": "rules saved; start_game when you begin playing"}


# ---- one game in progress --------------------------------------------------------------------
def start_game(name, fair=True):
    card = game_rules(name)
    with _lock:
        CURRENT.update(game=str(name), rules=card.get("rules", "") if card.get("found") else "",
                       notes=[], started=time.time(), fair=bool(fair))
    out = {"ok": True, "playing": str(name), "fair_play": bool(fair)}
    if not card.get("found"):
        out["note"] = "no rules card yet: learn the rules first (game_rules says how), then play"
    return out


def game_note(note):
    """One line in the notebook: the state you read, the move you made, what its result told you."""
    note = " ".join(str(note or "").split())[:400]
    if not note:
        return {"ok": False, "error": "write what you saw or did"}
    with _lock:
        if not CURRENT["game"]:
            return {"ok": False, "error": "no game in progress: start_game first"}
        CURRENT["notes"].append(f"{time.strftime('%H:%M:%S')} {note}")
        CURRENT["notes"] = CURRENT["notes"][-NOTE_LINES:]
        n = len(CURRENT["notes"])
    return {"ok": True, "notes": n}


def end_game(result=""):
    with _lock:
        game, CURRENT["game"] = CURRENT["game"], None
        CURRENT["notes"], CURRENT["rules"] = [], ""
    return {"ok": True, "ended": game, "result": str(result or "")}


def prompt_block():
    """Put in front of the model on every step while a game is on."""
    with _lock:
        if not CURRENT["game"]:
            return ""
        rules = CURRENT["rules"] or "(no rules card yet -- learn the rules before playing)"
        notes = "\n".join(CURRENT["notes"][-NOTE_LINES:]) or "(nothing yet)"
        fair = ("FAIR PLAY: you find the moves yourself; do not look up answers, solutions or hints."
                if CURRENT["fair"] else "The person allowed looking up answers.")
        return (f"GAME IN PROGRESS: {CURRENT['game']}. {fair}\n"
                f"RULES CARD:\n{rules}\n"
                f"YOUR NOTEBOOK (what you have seen and done; trust it over memory, add to it with game_note "
                f"after every move -- the move, then what the result showed):\n{notes}\n"
                "A game on screen is PLAYED BY operate_computer, in one goal that includes getting the board "
                "ready and \"play until it is won or over\" -- do not make the moves yourself one call at a time "
                "(you run out of steps; live test 2026-10-28). Only a game with no screen of its own is played "
                "move by move here: read the state, compare with the notebook, ONE move by the rules, check it, "
                "note it.")


# ---- fair play ------------------------------------------------------------------------------
_CHEAT = re.compile(r"\b(answer|answers|solution|solutions|solve[dr]?|hint|hints|cheat|spoiler|today'?s (word|puzzle)"
                    r"|word of the day|walkthrough)\b", re.I)
_ASKED_FOR_IT = re.compile(r"\b(look (it )?up|find|tell me|give me|show me|search for)\b.{0,30}\b(answer|solution|"
                           r"hint|cheat)\b|\bcheat\b", re.I)


def fair_play_check(tool_name, args, turn_text=""):
    """A refusal result when a lookup would be cheating, else None."""
    with _lock:
        playing, fair, game = CURRENT["game"], CURRENT["fair"], CURRENT["game"]
    if not playing or not fair:
        return None
    if not re.search(r"search|read_web_page|browser_open|navigate", tool_name or ""):
        return None
    blob = " ".join(str(v) for v in (args or {}).values())
    if not _CHEAT.search(blob):
        return None
    if _ASKED_FOR_IT.search(str(turn_text or "")):
        return None
    log.info(f"Fair play: refused {tool_name}({blob[:80]!r}) during {game}")
    return {"ok": False, "refused": "fair play",
            "error": (f"Not while playing {game} fairly: looking up answers, solutions or hints is cheating. Work "
                      "the move out yourself from the rules card and the state on screen. (Looking up the RULES is "
                      "fine; the person can also allow answers by saying so.)")}
