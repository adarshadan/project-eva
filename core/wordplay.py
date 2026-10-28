"""Eva core -- keeping track of what a word game's board has told her, the way a careful player does.

2026-10-18: "I want her to be able to play games like Wordle like a human would ... she is very poor with
multi-step reasoning". In letter-feedback games (Wordle and its kind) every guess colours its letters:
in the word at that place (correct / green), in the word somewhere else (present / yellow), or not in it
(absent / grey). A person keeps all of it in mind; the model forgot half by the third row, and guessed
words that the board had already ruled out.

This reads the board's own labels (Wordle publishes "1st letter, C, absent" for every tile), states what
is KNOWN in plain words for the planner, and checks a planned guess against it. It never suggests a word
and never looks anything up: choosing the word stays her reasoning, as it would be a person's.
"""
import re

_TILE = re.compile(r"(\d)(?:st|nd|rd|th)\s+letter,\s*([A-Za-z])\s*,\s*(correct|present|absent|"
                   r"present in another position|not in word|in the word and in the correct spot|"
                   r"in the word but in the wrong spot|empty)", re.I)
STATE = {"correct": "correct", "in the word and in the correct spot": "correct", "present": "present",
         "present in another position": "present", "in the word but in the wrong spot": "present",
         "absent": "absent", "not in word": "absent"}


def parse_board(texts):
    """[row texts] -> [(WORD, [state, ...])] for every completed row of tiles, top to bottom."""
    guesses = []
    for row in texts or []:
        tiles = {}
        for m in _TILE.finditer(str(row)):
            state = STATE.get(m.group(3).lower())
            if state:
                tiles[int(m.group(1))] = (m.group(2).upper(), state)
        if tiles and sorted(tiles) == list(range(1, len(tiles) + 1)) and len(tiles) >= 4:
            word = "".join(tiles[i][0] for i in sorted(tiles))
            guesses.append((word, [tiles[i][1] for i in sorted(tiles)]))
    return guesses


_TYPED = re.compile(r"(\d)(?:st|nd|rd|th)\s+letter,\s*([A-Za-z])\b(?!\s*,\s*[a-z])", re.I)


def pending(texts):
    """Letters typed into the active row but not scored ("1st letter, Q" -- a scored tile also names its
    state: "1st letter, C, absent"). Live Wordle 2026-10-28: a stray click put Q before CRANE; "QCRAN" was
    rejected ("Not in word list") and stayed in its row, and every new guess went nowhere."""
    tiles = {}
    for row in texts or []:
        for m in _TYPED.finditer(str(row)):
            tiles.setdefault(int(m.group(1)), m.group(2).upper())
    if not tiles:
        return ""
    return "".join(tiles[i] for i in sorted(tiles))


def pending_note(word, length=5):
    if not word:
        return ""
    return (f"TYPED BUT NOT SCORED: the active row already holds {word}. If the game rejected it (e.g. "
            f"'Not in word list') or it is wrong, delete it first: key Backspace {len(word)} times (one key action "
            f"with the keys repeated), then type the next guess. To submit it as it is, press Enter.")


def known(guesses):
    """What the board has established: greens, yellows (with the places they are NOT), minimum counts,
    letters that are out."""
    n = len(guesses[0][0]) if guesses else 5
    green, not_at, at_least, out = {}, {}, {}, set()
    for word, states in guesses:
        count = {}
        for i, (ch, st) in enumerate(zip(word, states)):
            if st == "correct":
                green[i] = ch
                count[ch] = count.get(ch, 0) + 1
            elif st == "present":
                not_at.setdefault(ch, set()).add(i)
                count[ch] = count.get(ch, 0) + 1
        for ch, c in count.items():
            at_least[ch] = max(at_least.get(ch, 0), c)
        for i, (ch, st) in enumerate(zip(word, states)):
            if st == "absent":
                if count.get(ch):
                    not_at.setdefault(ch, set()).add(i)       # a second copy is not there
                else:
                    out.add(ch)
    out -= set(at_least)
    return {"length": n, "green": green, "not_at": not_at, "at_least": at_least, "out": out}


def summary(guesses):
    if not guesses:
        return ""
    k = known(guesses)
    n = k["length"]
    pattern = "".join(k["green"].get(i, "_") for i in range(n))
    bits = [f"Guesses so far: {', '.join(w for w, _ in guesses)} ({len(guesses)} used).",
            f"Pattern of letters in place: {pattern}."]
    yel = [f"{ch} (in the word, but not at place {', '.join(str(i + 1) for i in sorted(pos))})"
           for ch, pos in sorted(k["not_at"].items()) if ch in k["at_least"]]
    if yel:
        bits.append("Also in the word: " + "; ".join(yel) + ".")
    twice = [ch for ch, c in k["at_least"].items() if c > 1]
    if twice:
        bits.append("Appears at least twice: " + ", ".join(sorted(twice)) + ".")
    if k["out"]:
        bits.append("Not in the word: " + " ".join(sorted(k["out"])) + ".")
    bits.append("The next guess must fit ALL of this.")
    return " ".join(bits)


def check(word, guesses):
    """(ok, why): does a planned guess fit everything the board has shown?"""
    w = re.sub(r"[^A-Za-z]", "", str(word or "")).upper()
    if not guesses:
        return True, ""
    k = known(guesses)
    if len(w) != k["length"]:
        return False, f"{w} has {len(w)} letters; the word has {k['length']}"
    if w in {g for g, _ in guesses}:
        return False, f"{w} was already guessed"
    for i, ch in k["green"].items():
        if w[i] != ch:
            return False, f"place {i + 1} must be {ch}"
    for ch in k["out"]:
        if ch in w:
            return False, f"{ch} is not in the word"
    for ch, c in k["at_least"].items():
        if w.count(ch) < c:
            return False, f"{ch} must be in it" + (f" at least {c} times" if c > 1 else "")
    for ch, places in k["not_at"].items():
        for i in places:
            if w[i] == ch:
                return False, f"{ch} is not at place {i + 1}"
    return True, ""


def planned_word(next_step, length):
    """The word a planned step means to type: a quoted word, else a capitalised one of the right length."""
    s = str(next_step or "")
    m = re.search(r"['\"“‘]([A-Za-z]{%d})['\"”’]" % length, s)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b([A-Z]{%d})\b" % length, s)
    return m.group(1) if m else ""
