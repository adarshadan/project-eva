"""Learning a game from its rules and playing it move by move (game_play.py), and reading a
window's elements row by row (structure.read_elements)."""
import game_play as gp
import structure
from core.effects import Effect as E, Surface as S
from core.registry import BOOL, HANDLE, STR, register

register("game_rules", "What you know about a game's rules (a saved rules card). If you have none, it tells you how to "
         "learn them: look the RULES up on the web (never answers or solutions), then save_game_rules.",
         {"name": STR("The game, e.g. 'Wordle', 'Sudoku', 'Minesweeper', 'chess'.")}, ["name"],
         surface=S.SELF, effect=E.OBSERVE, fn=gp.game_rules, embodied=False)
register("save_game_rules", "Save what you learned about a game as a short rules card for playing it on this computer: "
         "GOAL; HOW A MOVE IS MADE here (keys/clicks, what confirms a move); HOW TO READ THE STATE on screen; HOW IT "
         "ENDS; a sound BASIC STRATEGY. Reused next time.",
         {"name": STR(), "rules": STR("The rules card, plain text."), "sources": {"type": "array", "items": {"type": "string"},
                                                                               "description": "Pages you read."}},
         ["name", "rules"], surface=S.SELF, effect=E.OBSERVE, fn=gp.save_game_rules, embodied=False)
register("start_game", "Begin playing a game: its rules card and your notebook are then kept in front of you on every "
         "step. Fair play (no looking up answers) unless the person asked for answers.",
         {"name": STR(), "fair": BOOL("Default true. False only if the person said you may look up answers.")},
         ["name"], surface=S.SELF, effect=E.OBSERVE, fn=gp.start_game, embodied=False)
register("game_note", "Write one line in the game notebook after every move: the state you read, the move you made, "
         "and what the result told you (e.g. 'Row 1 CRANE: C absent, R present, A correct, N absent, E absent').",
         {"note": STR()}, ["note"], surface=S.SELF, effect=E.OBSERVE, fn=gp.game_note, embodied=False)
register("end_game", "The game is over (won, lost, or the person stopped it).", {"result": STR()},
         surface=S.SELF, effect=E.OBSERVE, fn=gp.end_game, embodied=False)
register("read_screen_elements", "Read every labelled element of a window ROW BY ROW from its structure -- a web "
         "game's tiles and keys, a form's fields, a table, a list -- exactly, with roles and disabled state. The "
         "first way to read a game board or a page's state; look_at_screen only if this has nothing.",
         {"window_handle": HANDLE, "contains": STR("Only elements whose label contains these words (optional).")},
         surface=S.SCREEN, effect=E.OBSERVE, fn=lambda window_handle=None, contains="":
         structure.read_elements(window_handle, contains), verb="look", embodied=False, deadline=20)
