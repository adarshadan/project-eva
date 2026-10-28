"""Eva -- turn-based games: read the board, solve, move, wait for the reply.

The model only starts the game. Each turn the board is READ from the screen
(OpenCV, in any program: a browser game, a desktop app), the move is
COMPUTED by a real solver -- never guessed by the language model -- the move
is made with the mouse or keys, and she waits until the screen shows the
opponent's reply (a person or the game's own computer player).

  tictactoe     grid lines -> 3 x 3 cells; perfect play (minimax)
  connect_four  the disc holes -> 6 x 7; alpha-beta search, 6 plies
  2048          the tile board -> 4 x 4 values (classic colours, OCR as the
                fallback); expectimax, arrow keys

Who owns a piece is not guessed from its colour or shape: the cells she
filled herself are hers, every other filled cell is the opponent's. So any
style of X and O, any disc colours, work.
"""
import functools
import random
import time

import activities
import perception
from logger import get_logger

log = get_logger(__name__)


def _np():
    import numpy as np
    return np


def _cv():
    import cv2
    return cv2


# ---------------------------------------------------------------------------------------
# Seeing boards
# ---------------------------------------------------------------------------------------
def _background(img):
    """The dominant colour (the page behind the board)."""
    np = _np()
    small = img[::4, ::4].reshape(-1, 3) // 16
    vals, counts = np.unique(small, axis=0, return_counts=True)
    return vals[counts.argmax()] * 16 + 8


def _ink(img, bg, thr=60):
    np = _np()
    return (np.abs(img.astype(np.int16) - bg.astype(np.int16)).max(axis=2) > thr)


def _runs(profile, min_len):
    """Groups of consecutive indices where profile >= min_len -> centre positions."""
    out, start = [], None
    for i, v in enumerate(list(profile) + [0]):
        if v >= min_len and start is None:
            start = i
        elif v < min_len and start is not None:
            out.append(((start + i - 1) / 2.0, i - start))
            start = None
    return out


def find_line_grid(img, n=3):
    """An n x n grid drawn with lines (tic-tac-toe and the like): returns
    {"cells": [[(x0, y0, x1, y1)]*n]*n} in image pixels, or None."""
    np, cv2 = _np(), _cv()
    bg = _background(img)
    ink = _ink(img, bg).astype(np.uint8)
    h, w = ink.shape
    best = None
    for frac in (0.20, 0.14, 0.09):
        k = max(15, int(min(h, w) * frac))
        hor = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
        ver = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k)))
        rows = _runs(hor.sum(axis=1), k)
        cols = _runs(ver.sum(axis=0), k)
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                dy = rows[j][0] - rows[i][0]
                if dy < 20:
                    continue
                for a in range(len(cols)):
                    for b in range(a + 1, len(cols)):
                        dx = cols[b][0] - cols[a][0]
                        if dx < 20 or abs(dx - dy) > 0.25 * max(dx, dy):
                            continue
                        # the two lines of each pair must cross the other pair's span
                        y0, y1 = int(rows[i][0] - dy), int(rows[j][0] + dy)
                        x0, x1 = int(cols[a][0] - dx), int(cols[b][0] + dx)
                        span_h = hor[int(rows[i][0]), max(0, x0):min(w, x1)].mean() if 0 <= rows[i][0] < h else 0
                        span_v = ver[max(0, y0):min(h, y1), int(cols[a][0])].mean() if 0 <= cols[a][0] < w else 0
                        if span_h < 0.6 or span_v < 0.6:
                            continue
                        score = dx * dy * (span_h + span_v)
                        if best is None or score > best[0]:
                            best = (score, rows[i][0], rows[j][0], cols[a][0], cols[b][0], dx, dy)
        if best:
            break
    if not best:
        return None
    _, r1, r2, c1, c2, dx, dy = best
    ys = [r1 - dy, r1, r2, r2 + dy]
    xs = [c1 - dx, c1, c2, c2 + dx]
    cells = [[(int(max(0, xs[c])), int(max(0, ys[r])), int(min(w, xs[c + 1])), int(min(h, ys[r + 1])))
              for c in range(3)] for r in range(3)]
    return {"cells": cells, "bg": bg}


def classify_mark(img, cell, bg):
    """'' (empty), 'X' or 'O' for one tic-tac-toe cell. Shape, not colour:
    an X crosses in the middle, an O is hollow."""
    np = _np()
    x0, y0, x1, y1 = cell
    mx, my = int((x1 - x0) * 0.17), int((y1 - y0) * 0.17)
    crop = img[y0 + my:y1 - my, x0 + mx:x1 - mx]
    if crop.size == 0:
        return ""
    local_bg = np.median(np.concatenate([crop[0], crop[-1], crop[:, 0], crop[:, -1]]), axis=0)
    ink = _ink(crop, local_bg, 70)
    if ink.mean() < 0.025:
        return ""
    ch, cw = ink.shape
    centre = ink[int(ch * 0.4):int(ch * 0.6), int(cw * 0.4):int(cw * 0.6)]
    return "X" if centre.mean() > 0.12 else "O"


def read_tictactoe(img):
    g = find_line_grid(img)
    if not g:
        return None
    board = [[classify_mark(img, g["cells"][r][c], g["bg"]) for c in range(3)] for r in range(3)]
    centres = [[((x0 + x1) // 2, (y0 + y1) // 2) for (x0, y0, x1, y1) in row] for row in g["cells"]]
    return {"board": board, "centres": centres}


def read_connect_four(img, rows=6, cols=7):
    """The holes of a Connect Four board -> {"board": rows x cols of '' or a
    colour key, "centres", "col_x"}; None if not found."""
    np, cv2 = _np(), _cv()
    h, w = img.shape[:2]
    grey = cv2.medianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 5)
    best = None
    for dp in (1.2, 1.5):
        circles = cv2.HoughCircles(grey, cv2.HOUGH_GRADIENT, dp=dp, minDist=max(8, min(w, h) // 16),
                                   param1=90, param2=24, minRadius=max(5, min(w, h) // 40),
                                   maxRadius=max(10, min(w, h) // 9))
        if circles is None:
            continue
        cs = circles[0]
        # keep the dominant radius (the holes are all the same size)
        r_med = float(np.median(cs[:, 2]))
        cs = cs[np.abs(cs[:, 2] - r_med) < 0.25 * r_med]
        if len(cs) >= rows * cols * 0.8:
            best = cs
            break
        if best is None or len(cs) > len(best):
            best = cs
    if best is None or len(best) < rows * cols * 0.7:
        return None

    def clusters(vals, n):
        vals = sorted(vals)
        gaps = sorted(((vals[i + 1] - vals[i], i) for i in range(len(vals) - 1)), reverse=True)[:n - 1]
        cuts = sorted(i for _, i in gaps)
        groups, start = [], 0
        for c in cuts + [len(vals) - 1]:
            groups.append(vals[start:c + 1])
            start = c + 1
        return [sum(g) / len(g) for g in groups if g]
    xs = clusters([c[0] for c in best], cols)
    ys = clusters([c[1] for c in best], rows)
    if len(xs) != cols or len(ys) != rows:
        return None
    r = float(np.median(best[:, 2]))
    samples = []
    for yy in ys:
        row = []
        for xx in xs:
            patch = img[int(yy - r * 0.45):int(yy + r * 0.45), int(xx - r * 0.45):int(xx + r * 0.45)]
            row.append(patch.reshape(-1, 3).mean(axis=0) if patch.size else np.zeros(3))
        samples.append(row)
    flat = np.array([s for row in samples for s in row])
    # Empty holes: the most common colour (at the start of a game nearly all
    # are); a disc is anything clearly different from it.
    keys = [tuple(k) for k in (flat // 40).astype(int)]
    empty_key = max(set(keys), key=keys.count)
    empty_col = flat[[n for n, k in enumerate(keys) if k == empty_key]].mean(axis=0)
    board = [["" if np.abs(s - empty_col).max() < 45 else _colour_name(s) for s in row] for row in samples]
    centres = [[(int(xx), int(yy)) for xx in xs] for yy in ys]
    return {"board": board, "centres": centres, "col_x": [int(x) for x in xs]}


def _colour_name(bgr):
    import colorsys
    b, g, r = [float(v) / 255 for v in bgr]
    hh, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.25:
        return "dark" if v < 0.4 else "light"
    deg = hh * 360
    for limit, name in ((15, "red"), (45, "orange"), (70, "yellow"), (170, "green"), (260, "blue"), (330, "purple")):
        if deg < limit:
            return name
    return "red"


# 2048: the classic palette (gabrielecirulli/2048 and most clones)
TILE_COLOURS = {0: (180, 193, 205), 2: (218, 228, 238), 4: (200, 224, 237), 8: (121, 177, 242),
                16: (99, 149, 245), 32: (95, 124, 246), 64: (59, 94, 246), 128: (114, 207, 237),
                256: (97, 204, 237), 512: (80, 200, 237), 1024: (63, 197, 237), 2048: (46, 194, 237)}
BOARD_COLOUR = (160, 173, 187)


def read_2048(img):
    """The 4 x 4 board of 2048 -> {"board": values (0 = empty)}; None if no
    board of the classic colours is in view (then OCR per tile is tried)."""
    np, cv2 = _np(), _cv()
    mask = (np.abs(img.astype(np.int16) - np.array(BOARD_COLOUR, np.int16)).max(axis=2) < 18).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n < 2:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = stats[i, :4]
    if w < 80 or h < 80 or abs(w - h) > 0.15 * max(w, h):
        return None
    gap = w * 0.03
    cell = (w - 5 * gap) / 4
    board, centres = [], []
    for r in range(4):
        row, crow = [], []
        for c in range(4):
            cx = int(x + gap + c * (cell + gap) + cell / 2)
            cy = int(y + gap + r * (cell + gap) + cell / 2)
            patch = img[cy - int(cell * 0.3):cy + int(cell * 0.3), cx - int(cell * 0.3):cx + int(cell * 0.3)]
            # the tile colour: the most common colour in the patch (digits are a minority)
            px = (patch.reshape(-1, 3) // 8).astype(int)
            vals, counts = np.unique(px, axis=0, return_counts=True)
            col = vals[counts.argmax()] * 8 + 4
            dist = {v: float(np.abs(col - np.array(c3)).sum()) for v, c3 in TILE_COLOURS.items()}
            best = min(dist, key=dist.get)
            if dist[best] > 60:                   # a colour beyond the classic set: read the number
                txt = "".join(ch for ch in perception.read_text(patch) if ch.isdigit())
                best = int(txt) if txt else (4096 if dist[best] > 60 else best)
            row.append(best)
            crow.append((cx, cy))
        board.append(row)
        centres.append(crow)
    return {"board": board, "centres": centres, "rect": (int(x), int(y), int(x + w), int(y + h))}


# ---------------------------------------------------------------------------------------
# Solvers
# ---------------------------------------------------------------------------------------
TTT_LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]


def ttt_winner(cells):
    for a, b, c in TTT_LINES:
        if cells[a] and cells[a] == cells[b] == cells[c]:
            return cells[a]
    return "draw" if all(cells) else None


_OTHER = {"X": "O", "O": "X", "M": "T", "T": "M"}


@functools.lru_cache(maxsize=None)
def _ttt_score(cells, me, turn):
    w = ttt_winner(cells)
    if w == me:
        return 10
    if w == "draw":
        return 0
    if w:
        return -10
    other = _OTHER[turn]
    scores = []
    for i in range(9):
        if not cells[i]:
            nxt = cells[:i] + (turn,) + cells[i + 1:]
            s = _ttt_score(nxt, me, other)
            scores.append(s - 1 if s > 0 else s + 1 if s < 0 else 0)   # prefer quick wins, slow losses
    return max(scores) if turn == me else min(scores)


def ttt_best(cells, me):
    """Index 0..8 of the best move for `me` ('X'/'O'), perfect play."""
    cells = tuple(cells)
    other = _OTHER[me]
    best, best_s = None, -99
    for i in [4, 0, 2, 6, 8, 1, 3, 5, 7]:
        if not cells[i]:
            s = _ttt_score(cells[:i] + (me,) + cells[i + 1:], me, other)
            if s > best_s:
                best, best_s = i, s
    return best


C4_ROWS, C4_COLS = 6, 7


def c4_drop(board, col, piece):
    for r in range(C4_ROWS - 1, -1, -1):
        if not board[r][col]:
            nb = [row[:] for row in board]
            nb[r][col] = piece
            return nb, r
    return None, None


def c4_winner(board):
    for r in range(C4_ROWS):
        for c in range(C4_COLS):
            p = board[r][c]
            if not p:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                if all(0 <= r + k * dr < C4_ROWS and 0 <= c + k * dc < C4_COLS and board[r + k * dr][c + k * dc] == p
                       for k in range(4)):
                    return p
    return "draw" if all(board[0]) else None


def _c4_eval(board, me, them):
    score = 0
    for r in range(C4_ROWS):
        if board[r][3] == me:
            score += 3
    for r in range(C4_ROWS):
        for c in range(C4_COLS):
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                cells = [(r + k * dr, c + k * dc) for k in range(4)]
                if not all(0 <= rr < C4_ROWS and 0 <= cc < C4_COLS for rr, cc in cells):
                    continue
                w = [board[rr][cc] for rr, cc in cells]
                m, t, e = w.count(me), w.count(them), w.count("")
                if m == 3 and e == 1:
                    score += 5
                elif m == 2 and e == 2:
                    score += 2
                if t == 3 and e == 1:
                    score -= 4
    return score


def _c4_negamax(board, depth, alpha, beta, me, them):
    w = c4_winner(board)
    if w == me:
        return 100000 + depth
    if w == them:
        return -100000 - depth
    if w == "draw":
        return 0
    if depth == 0:
        return _c4_eval(board, me, them)
    best = -10 ** 9
    for col in (3, 2, 4, 1, 5, 0, 6):
        nb, _ = c4_drop(board, col, me)
        if nb is None:
            continue
        s = -_c4_negamax(nb, depth - 1, -beta, -alpha, them, me)
        best = max(best, s)
        alpha = max(alpha, s)
        if alpha >= beta:
            break
    return best


def c4_best(board, me, them, depth=6):
    best_col, best_s = None, -10 ** 9
    for col in (3, 2, 4, 1, 5, 0, 6):
        nb, _ = c4_drop(board, col, me)
        if nb is None:
            continue
        s = -_c4_negamax(nb, depth - 1, -10 ** 9, 10 ** 9, them, me)
        if s > best_s:
            best_col, best_s = col, s
    return best_col


def _slide_row(row):
    tiles = [v for v in row if v]
    out, score, i = [], 0, 0
    while i < len(tiles):
        if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
            out.append(tiles[i] * 2)
            score += tiles[i] * 2
            i += 2
        else:
            out.append(tiles[i])
            i += 1
    return out + [0] * (4 - len(out)), score


def g2048_move(board, d):
    """d in left/right/up/down -> (new board, moved?)"""
    b = [row[:] for row in board]
    if d in ("up", "down"):
        b = [list(r) for r in zip(*b)]
    if d in ("right", "down"):
        b = [r[::-1] for r in b]
    nb = [_slide_row(r)[0] for r in b]
    if d in ("right", "down"):
        nb = [r[::-1] for r in nb]
    if d in ("up", "down"):
        nb = [list(r) for r in zip(*nb)]
    return nb, nb != board


def _g2048_heur(b):
    import math
    empty = sum(v == 0 for row in b for v in row)
    logs = [[math.log2(v) if v else 0 for v in row] for row in b]
    mono = 0
    for row in logs + [list(c) for c in zip(*logs)]:
        inc = sum(max(0, row[i + 1] - row[i]) for i in range(3))
        dec = sum(max(0, row[i] - row[i + 1]) for i in range(3))
        mono -= min(inc, dec)
    smooth = 0
    for r in range(4):
        for c in range(4):
            if logs[r][c]:
                if c < 3 and logs[r][c + 1]:
                    smooth -= abs(logs[r][c] - logs[r][c + 1])
                if r < 3 and logs[r + 1][c]:
                    smooth -= abs(logs[r][c] - logs[r + 1][c])
    mx = max(v for row in b for v in row)
    corner = 1.0 if mx in (b[0][0], b[0][3], b[3][0], b[3][3]) else 0.0
    return empty * 2.7 + mono * 1.0 + smooth * 0.1 + corner * 2.0 + math.log2(mx or 1) * 0.3


def _g2048_chance(b, depth, rng):
    empties = [(r, c) for r in range(4) for c in range(4) if not b[r][c]]
    if not empties:
        return _g2048_heur(b)
    if len(empties) > 5:
        empties = rng.sample(empties, 5)
    total = 0.0
    for (r, c) in empties:
        for v, p in ((2, 0.9), (4, 0.1)):
            nb = [row[:] for row in b]
            nb[r][c] = v
            total += p * _g2048_max(nb, depth - 1, rng)
    return total / len(empties)


def _g2048_max(b, depth, rng):
    if depth <= 0:
        return _g2048_heur(b)
    best = None
    for d in ("left", "up", "right", "down"):
        nb, moved = g2048_move(b, d)
        if moved:
            s = _g2048_chance(nb, depth, rng)
            best = s if best is None else max(best, s)
    return best if best is not None else -1e6


def g2048_best(board, depth=2, seed=0):
    rng = random.Random(seed)
    best, best_s = None, None
    for d in ("left", "up", "right", "down"):
        nb, moved = g2048_move(board, d)
        if moved:
            s = _g2048_chance(nb, depth, rng)
            if best_s is None or s > best_s:
                best, best_s = d, s
    return best


# ---------------------------------------------------------------------------------------
# Playing
# ---------------------------------------------------------------------------------------
class GameActivity(activities.Activity):
    kind = "game"
    GAMES = ("tictactoe", "connect_four", "2048")

    def __init__(self, game, region, window=None, first=None, max_seconds=900, grab=None, hands=None,
                 reply_timeout=180):
        names = {"tictactoe": "Tic-tac-toe", "connect_four": "Connect Four", "2048": "2048"}
        super().__init__(f"Playing {names.get(game, game)}", max_seconds, window=window, region=region)
        self.game = game
        self.first = first                      # True: she moves first; False: the opponent; None: work it out
        self._grab = grab or perception.grab_once
        self._hands = hands
        self.reply_timeout = reply_timeout
        self.moves = 0

    # screen <-> image
    def _shot(self):
        return self._grab(self.region)

    def _click_img(self, pt):
        x, y = pt
        (self._hands or perception.hands()).click(self.region[0] + x, self.region[1] + y)

    def _read(self, reader, tries=4, ctx=None):
        for _ in range(tries):
            st = reader(self._shot())
            if st:
                return st
            if ctx is not None and not ctx.alive(0.4):
                return None
        return None

    def run(self, ctx):
        {"tictactoe": self._tictactoe, "connect_four": self._connect_four, "2048": self._g2048}[self.game](ctx)

    def _wait_change(self, ctx, reader, key, timeout, poll=0.3):
        t0 = time.monotonic()
        while ctx.alive(poll):
            st = reader(self._shot())
            if st and key(st):
                return st
            if time.monotonic() - t0 > timeout:
                return None
        return None

    # ---- tic-tac-toe ----------------------------------------------------------------------
    def _tictactoe(self, ctx):
        st = self._read(read_tictactoe, ctx=ctx)
        if not st:
            self.outcome, self.result = "failed", "I could not find a tic-tac-toe board in that window"
            return
        flat = lambda s: tuple(c for row in s["board"] for c in row)            # noqa: E731
        mine = set()
        cells = flat(st)
        if any(cells) and self.first is None:
            # joining a game in progress: she plays the side to move (X moves first)
            xs, os_ = cells.count("X"), cells.count("O")
            me_label = "X" if xs == os_ else "O"
            mine = {i for i, c in enumerate(cells) if c == me_label}
            my_turn_first = me_label == "X"
        else:
            my_turn_first = True if self.first is None else bool(self.first)
        while ctx.alive():
            cells = flat(st)
            filled = {i for i, c in enumerate(cells) if c}
            theirs = filled - mine
            owner = tuple("M" if i in mine else "T" if i in theirs else "" for i in range(9))
            w = ttt_winner(owner)
            if w:
                self.outcome = {"M": "won", "T": "lost", "draw": "draw"}[w]
                self.result = {"won": "I won!", "lost": "you got me -- I lost", "draw": "it's a draw"}[self.outcome]
                return
            my_turn = (len(mine) == len(theirs)) if my_turn_first else (len(theirs) > len(mine))
            if my_turn:
                move = ttt_best(owner, "M")
                if move is None:
                    self.outcome, self.result = "draw", "it's a draw"
                    return
                self._click_img(st["centres"][move // 3][move % 3])
                self.moves += 1
                ctx.progress(f"move {self.moves}")
                st2 = self._wait_change(ctx, read_tictactoe, lambda s: flat(s)[move], 4)
                if st2 is None:                       # the click did not take: once more
                    self._click_img(st["centres"][move // 3][move % 3])
                    st2 = self._wait_change(ctx, read_tictactoe, lambda s: flat(s)[move], 4)
                    if st2 is None:
                        if ctx.stop_event.is_set():
                            return
                        self.outcome, self.result = "failed", "my move did not appear on the board"
                        return
                mine.add(move)
                st = st2
            else:
                ctx.progress(f"your turn (move {self.moves})")
                before = len(filled)
                st2 = self._wait_change(ctx, read_tictactoe,
                                        lambda s: sum(1 for c in flat(s) if c) > before or
                                        ttt_winner(tuple(flat(s))) is not None, self.reply_timeout)
                if st2 is None:
                    if ctx.stop_event.is_set():
                        return
                    self.outcome, self.result = "stopped", "no reply came, so I stopped"
                    return
                st = st2
                time.sleep(0.25)
                st = self._read(read_tictactoe, ctx=ctx) or st

    # ---- connect four -------------------------------------------------------------------------
    def _connect_four(self, ctx):
        st = self._read(read_connect_four, ctx=ctx)
        if not st:
            self.outcome, self.result = "failed", "I could not find a Connect Four board in that window"
            return
        mine = set()
        start_filled = {(r, c) for r in range(C4_ROWS) for c in range(C4_COLS) if st["board"][r][c]}
        my_first = self.first if self.first is not None else (not start_filled)
        while ctx.alive():
            filled = {(r, c) for r in range(C4_ROWS) for c in range(C4_COLS) if st["board"][r][c]}
            theirs = filled - mine
            owner = [["M" if (r, c) in mine else "T" if (r, c) in theirs else "" for c in range(C4_COLS)]
                     for r in range(C4_ROWS)]
            w = c4_winner(owner)
            if w:
                self.outcome = {"M": "won", "T": "lost", "draw": "draw"}[w]
                self.result = {"won": "I won!", "lost": "you got me -- I lost", "draw": "it's a draw"}[self.outcome]
                return
            my_turn = (len(mine) == len(theirs)) if my_first else (len(theirs) > len(mine))
            if my_turn:
                col = c4_best(owner, "M", "T")
                if col is None:
                    self.outcome, self.result = "draw", "it's a draw"
                    return
                _, row = c4_drop(owner, col, "M")
                self._click_img(st["centres"][0][col])
                self.moves += 1
                ctx.progress(f"move {self.moves}")
                st2 = self._wait_change(ctx, read_connect_four, lambda s: bool(s["board"][row][col]), 5)
                if st2 is None:
                    self.outcome, self.result = ("stopped", "stopped") if ctx.stop_event.is_set() else \
                        ("failed", "my disc did not appear on the board")
                    return
                mine.add((row, col))
                st = st2
            else:
                ctx.progress(f"your turn (move {self.moves})")
                before = len(filled)
                st2 = self._wait_change(ctx, read_connect_four,
                                        lambda s: sum(1 for r in s["board"] for c in r if c) > before,
                                        self.reply_timeout)
                if st2 is None:
                    if not ctx.stop_event.is_set():
                        self.outcome, self.result = "stopped", "no reply came, so I stopped"
                    return
                time.sleep(0.6)                        # let the disc finish falling
                st = self._read(read_connect_four, ctx=ctx) or st2

    # ---- 2048 -------------------------------------------------------------------------------
    def _g2048(self, ctx):
        st = self._read(read_2048, ctx=ctx)
        if not st:
            self.outcome, self.result = "failed", "I could not find a 2048 board in that window"
            return
        keys = {"left": "left", "right": "right", "up": "up", "down": "down"}
        stuck = 0
        while ctx.alive():
            b = st["board"]
            move = g2048_best(b, depth=2, seed=self.moves)
            if move is None:
                break
            (self._hands or perception.hands()).tap(keys[move])
            self.moves += 1
            best_tile = max(v for row in b for v in row)
            if self.moves % 5 == 0:
                ctx.progress(f"{self.moves} moves, best tile {best_tile}")
            st2 = self._wait_change(ctx, read_2048, lambda s: s["board"] != b, 1.5, poll=0.05)
            if st2 is None:
                stuck += 1
                if stuck >= 3:
                    break
                st = self._read(read_2048, ctx=ctx) or st
                continue
            stuck = 0
            time.sleep(0.15)                           # the slide/merge animation
            st = self._read(read_2048, ctx=ctx) or st2
        best_tile = max(v for row in st["board"] for v in row)
        if self.outcome == "timeout":
            self.result = f"time's up after {self.moves} moves -- best tile {best_tile}"
        elif not ctx.stop_event.is_set():
            self.outcome = "won" if best_tile >= 2048 else "done"
            self.result = f"game over after {self.moves} moves -- best tile {best_tile}"
        else:
            self.result = f"stopped after {self.moves} moves -- best tile {best_tile}"
