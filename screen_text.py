"""Eva -- finding things on screen by the words they show, locally.

eva.log 2026-10-11 19:20-20:08: most of what the person asked her to click
was TEXT on screen -- "Back to tab", "Accept all", "Play", "Log in", a video
titled "I visited America's city with no laws", "Japan's Deer City". Every
one went to the vision model (5-90 s a call, often 503 busy) and several
were clicked in the wrong place. Text has an exact answer: the operating
system's own OCR (Windows.Media.Ocr through winocr; Tesseract if installed)
returns every word with its rectangle in well under a second, on this PC.

The words are matched forgivingly, because the description usually came
through speech recognition: "Japan's dear City" must find "Japan's Deer
City", "city with no loss" must find "city with no laws". Each word is
compared by spelling (difflib) and by SOUND (a small consonant key: dear /
deer -> "dr", loss / laws -> "ls"); a phrase scores by how well its words
line up with a run of words on one line.

find_text(description, img, origin) -> {"found": True, box, center, score,
"text"} or None (not text, or nothing close enough -- then vision looks).
"""
import difflib
import re

from logger import get_logger

log = get_logger(__name__)

# Words that describe WHAT KIND of thing it is, not what it says.
GENERIC = {"button", "btn", "link", "icon", "the", "a", "an", "field", "box", "text", "label", "thumbnail",
           "image", "picture", "video", "option", "menu", "item", "title", "that", "this", "called", "named",
           "saying", "says", "which", "with", "word", "words", "heading", "caption", "captions", "on", "of",
           "click", "tile", "card", "entry", "checkbox", "toggle", "tab", "panel", "shortcut", "app",
           "application", "program", "folder", "file", "my", "desktop", "taskbar", "screen", "window", "page",
           "in", "at", "from"}
KEEP_IF_ONLY = {"tab", "on", "of", "with", "a", "the", "in", "at", "from", "my"}       # "Back to tab": keep when it is part of the text
THRESHOLD = 0.8
SHORT_THRESHOLD = 0.93           # a single short word ("Play", "OK") must be (nearly) exact


def _norm(s):
    s = str(s or "").lower().replace("’", "'").replace("`", "'")
    return re.sub(r"[^a-z0-9' ]+", " ", s)


def _tokens(s):
    return [t.strip("'") for t in _norm(s).split() if t.strip("'")]


def sound_key(word):
    """A rough consonant skeleton: words that sound alike usually share it."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    w = w.replace("ph", "f").replace("ck", "k").replace("qu", "kw").replace("x", "ks")
    w = re.sub(r"c(?=[eiy])", "s", w)
    w = w.replace("c", "k").replace("z", "s").replace("q", "k")
    first, rest = w[0], re.sub(r"[aeiouhwy]", "", w[1:])
    key = first + rest
    return re.sub(r"(.)\1+", r"\1", key)


def word_score(a, b):
    if a == b:
        return 1.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if len(a) >= 3 and len(b) >= 3 and sound_key(a) == sound_key(b):
        r = max(r, 0.9)
    return r


def query_words(description):
    description = re.sub(r"(?<![\w'])((?:[a-z]\s+){2,}[a-z])(?![\w'])",
                         lambda m: "".join(m.group(1).split()), str(description or "").lower())
    words = _tokens(description)
    core = [w for w in words if w not in GENERIC]
    if not core:
        return []
    # put back connecting words that sit BETWEEN kept words ("back to tab", "city with no laws")
    out, idx = [], [i for i, w in enumerate(words) if w not in GENERIC]
    for i in range(idx[0], idx[-1] + 1):
        if words[i] not in GENERIC or words[i] in KEEP_IF_ONLY:
            out.append(words[i])
    return out


def query_variants(description):
    """Readings of the description to try: as said, without leading/trailing kind-words
    ("the ... button"), and only the meaningful words. "Back to tab" keeps "tab" when the
    screen says it; "Play button" becomes "Play"."""
    joined = re.sub(r"(?<![\w'])((?:[a-z]\s+){2,}[a-z])(?![\w'])",
                    lambda m: "".join(m.group(1).split()), str(description or "").lower())
    words = _tokens(joined)                              # "w i z t r e e" -> "wiztree"
    out = []

    def add(v):
        if v and v not in out and len(" ".join(v)) >= 2:
            out.append(v)
    add(words)
    a = 0
    while a < len(words) and words[a] in GENERIC:          # each step of trimming kind-words is a reading
        a += 1
        add(words[a:])
    for start in range(0, a + 1):
        b = len(words)
        while b > start and words[b - 1] in GENERIC:
            b -= 1
            add(words[start:b])
    add(query_words(description))
    return out


WORD_OK = 0.75                   # a query word counts as present at this word score
WHOLE_OK = 0.9                   # whole-phrase similarity alone only counts when this close ("log in" / "login")


def phrase_score(q, window):
    """How well query words q line up with OCR words `window` (in order).
    eva.log 2026-10-11 22:55: "team shortcut" scored 0.85 against "- Shortcut"
    (half the words, plus lenient whole-string similarity) and was clicked.
    Now most of the words said must really be there."""
    window = [w for w in window if re.search(r"[a-z0-9]", w)]          # "-", "|" are not words
    if not q or not window:
        return 0.0
    present = sum(1 for qw in q if max(word_score(qw, ww) for ww in window) >= WORD_OK)
    need = len(q) if len(q) <= 2 else -(-3 * len(q) // 4)               # all of 1-2 words, 3/4 of more
    whole = difflib.SequenceMatcher(None, " ".join(q), " ".join(window)).ratio()
    if present < need:
        return whole if whole >= WHOLE_OK else 0.0
    used, total, last = set(), 0.0, -1
    for qw in q:
        best, at = 0.0, None
        for j, ww in enumerate(window):
            if j in used:
                continue
            s = word_score(qw, ww)
            if j < last:
                s *= 0.85                                   # out of order: weaker
            if s > best:
                best, at = s, j
        if at is not None and best >= 0.6:            # a weak guess must not take a word another one needs
            used.add(at)
            last = at
        else:
            best = 0.0
        total += best
    s = total / len(q)
    extra = max(0, len(window) - len(q))
    s *= (len(q) / (len(q) + extra)) ** 0.25                # extra words on screen: slightly weaker
    return max(s, whole) if whole >= WHOLE_OK else s


# ---- OCR ---------------------------------------------------------------------------------
# One Windows OCR engine for the whole session (Phase 1: "persistent OCR engine", first read under
# 300 ms). winocr.recognize_pil_sync created a new engine for every read and then converted the
# result by reflecting over every line and word object (dir() on each); both are skipped here.
_OCR = {"engine": None, "lock": None, "off": False}


def _win_engine():
    import threading
    if _OCR["lock"] is None:
        _OCR["lock"] = threading.Lock()
    if _OCR["engine"] is None:
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.globalization import Language
        _OCR["engine"] = (OcrEngine.try_create_from_language(Language("en"))
                          or OcrEngine.try_create_from_user_profile_languages())
        if _OCR["engine"] is None:
            raise RuntimeError("no Windows OCR language installed")
    return _OCR["engine"]


def _win_ocr_words(img):
    import asyncio
    from winrt.windows.storage.streams import DataWriter
    from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
    engine = _win_engine()
    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    writer = DataWriter()
    writer.write_bytes(rgba.tobytes())
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.RGBA8,
                                                    rgba.width, rgba.height)

    async def read():
        return await engine.recognize_async(bitmap)
    with _OCR["lock"]:
        result = asyncio.run(read())
    out = []
    for n, line in enumerate(result.lines):
        for w in line.words:
            r = w.bounding_rect
            if w.text:
                out.append((str(w.text), float(r.x), float(r.y), float(r.width), float(r.height), n))
    return out


def warm():
    """Create the engine and read a tiny picture once, at start-up, so the first real read is fast."""
    try:
        from PIL import Image
        _win_ocr_words(Image.new("RGB", (64, 24), "white"))
        return True
    except Exception as e:
        log.info(f"OCR warm-up skipped: {e}")
        return False


def ocr_words(img, single_block=False):
    """[(text, left, top, width, height, line_no)] in IMAGE pixels, or None when
    no OCR engine is available. single_block: the picture is one control (a
    button), read as one block. Module function so checks can replace it."""
    if not _OCR["off"]:
        try:
            return _win_ocr_words(img)
        except ImportError:
            _OCR["off"] = True
        except Exception as e:
            log.info(f"Windows OCR (kept engine) failed ({e}) -- trying winocr")
    try:
        import winocr
        res = winocr.recognize_pil_sync(img, "en")
        lines = res.get("lines") if isinstance(res, dict) else getattr(res, "lines", None)
        out = []
        for n, line in enumerate(lines or []):
            words = line.get("words") if isinstance(line, dict) else getattr(line, "words", [])
            for w in words or []:
                text = w.get("text") if isinstance(w, dict) else getattr(w, "text", "")
                r = w.get("bounding_rect") if isinstance(w, dict) else getattr(w, "bounding_rect", None)
                if isinstance(r, dict):
                    x, y, ww, hh = r.get("x", 0), r.get("y", 0), r.get("width", 0), r.get("height", 0)
                else:
                    x, y, ww, hh = (getattr(r, k, 0) for k in ("x", "y", "width", "height"))
                if text:
                    out.append((str(text), float(x), float(y), float(ww), float(hh), n))
        return out
    except ImportError:
        pass
    except Exception as e:
        log.info(f"Windows OCR failed ({e}) -- trying Tesseract")
    try:
        import pytesseract
        d = pytesseract.image_to_data(img, config="--psm 6" if single_block else "--psm 11",
                                      output_type=pytesseract.Output.DICT)
        out = []
        for i, text in enumerate(d.get("text", [])):
            if str(text).strip() and float(d["conf"][i]) > 40:
                line = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
                out.append((text, d["left"][i], d["top"][i], d["width"][i], d["height"][i], hash(line)))
        return out
    except Exception:
        return None


_WORDS_CACHE = {}                 # fingerprint -> [words of pass 1, words of pass 2] for an unchanged screen


def islands(img, limit=30):
    """Button-like regions: filled blocks clearly lighter or darker than the page
    (a black "Play" pill on a light page), and outlined boxes (a "Log in" button
    drawn as a border). Whole-screen OCR layout often drops them; read one at a
    time they are easy. [(left, top, right, bottom)], smallest first."""
    import numpy as np
    import cv2
    g = np.asarray(img.convert("L"))
    med = float(np.median(g))
    boxes = []

    def ok(w, h):
        return 28 <= w <= 640 and 14 <= h <= 96 and w >= 1.2 * h

    for mask in ((g > med + 55), (g < med - 55)):
        m = mask.astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(m)
        for k in range(1, n):
            x, y, w, h, area = stats[k]
            if ok(w, h) and area >= 0.5 * w * h:
                boxes.append((int(x), int(y), int(x + w), int(y + h)))
    edges = cv2.dilate(cv2.Canny(g, 40, 120), np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if ok(w, h) and h >= 24 and cv2.contourArea(c) >= 0.6 * w * h:
            boxes.append((int(x), int(y), int(x + w), int(y + h)))

    def iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        inter = ix * iy
        return inter / float((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    out = []
    for b in sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1])):
        if all(iou(b, o) < 0.6 for o in out):
            out.append(b)
    return out[:limit]


def island_words(img):
    out, n = [], 0
    from PIL import ImageOps, ImageStat
    for (l, t, r, b) in islands(img):
        h, w = b - t, r - l
        # inside the button: rounded ends and the border confuse OCR ("C conirue")
        dx, dy = int(min(h / 2, w / 6)), 3
        il, it, ir, ib = l + dx, t + dy, r - dx, b - dy
        if ir - il < 10 or ib - it < 8:
            il, it, ir, ib = l, t, r, b
        crop = img.crop((il, it, ir, ib)).convert("L")
        if ImageStat.Stat(crop).mean[0] < 110:          # white text on a dark button: read it dark-on-light
            crop = ImageOps.invert(crop)
        k = 1.0
        if crop.height < 32:                            # small text reads better a little larger
            k = 32 / crop.height
            crop = crop.resize((int(crop.width * k), 32))
        pad = 12
        crop = ImageOps.expand(crop, border=pad, fill=255).convert("RGB")
        ws = ocr_words(crop, single_block=True) or []
        n += 1
        for (text, x, y, ww, hh, line) in ws:
            out.append((text, il + (x - pad) / k, it + (y - pad) / k, ww / k, hh / k, ("island", n, line)))
    return out


def ocr_passes(img, key=None):
    """Words from the picture as it is; then -- only if the words were not found --
    from it in inverted grey, then from each button-like region on its own: OCR
    engines miss dark-on-light buttons on a dark page and the reverse."""
    cached = _WORDS_CACHE.get(key) if key else None
    if cached is None:
        cached = []
        if key:
            if len(_WORDS_CACHE) > 8:
                _WORDS_CACHE.clear()
            _WORDS_CACHE[key] = cached
    for i in range(3):
        if i < len(cached):
            yield cached[i]
            continue
        if i == 0:
            w = ocr_words(img)
        elif i == 1:
            try:
                from PIL import ImageOps
                w = ocr_words(ImageOps.invert(img.convert("L")).convert("RGB"))
            except Exception:
                w = None
        else:
            try:
                w = island_words(img)
            except Exception as e:
                log.debug(f"button-region OCR skipped: {e}")
                w = None
        cached.append(w)
        if w is None and i == 0:
            return                                   # no OCR engine at all
        yield w


CONTROL_WORDS = {"button", "btn", "icon", "link", "tab", "menu", "checkbox", "toggle", "option", "item", "tile"}


def is_control(description):
    """A label to press ("Play", "the Continue button", "Accept all"), not a passage or title."""
    words = _tokens(description)
    return bool(set(words) & CONTROL_WORDS) or len(query_words(description)) <= 3


def find_text(description, img, origin=(0, 0), words=None, exclude=(), key=None):
    """The place on screen showing `description`, or None.

    A title or passage: the first OCR pass that has it wins. A CONTROL (a short
    label, or "... button"): every pass is read and the best LABEL wins -- eva.log
    2026-10-11 23:14: "Play button" matched a small "Play" link near the bottom
    (13 px) while the big white-on-black Play button had not been read by the first
    pass; "Wordle" matched "Wordle," inside a sentence. A word standing alone as a
    line, or inside a button-shaped region, beats the same word inside running text,
    and larger text beats smaller."""
    control = is_control(description)
    if words is not None:
        return find_one(description, img, origin, words, exclude, control)
    if not query_words(description):
        return None
    found = []
    for ws in ocr_passes(img, key):
        hit = find_one(description, img, origin, ws, exclude, control)
        if hit and not control:
            return hit
        if hit:
            found.append(hit)
    if not found:
        return None
    found.sort(key=lambda h: (-round(h["rank"], 2), -h["height"]))
    best = dict(found[0])
    others = list(best.get("also_at", []))
    for h in found[1:]:
        for c in [h] + [dict(o, rank=h["rank"]) for o in h.get("also_at", [])]:
            x, y = c.get("center_x", c.get("x")), c.get("center_y", c.get("y"))
            if c.get("rank", 0) < best["rank"] - 0.02:
                continue
            if abs(x - best["center_x"]) <= 40 and abs(y - best["center_y"]) <= 25:
                continue
            if all(abs(x - o["x"]) > 40 or abs(y - o["y"]) > 25 for o in others):
                others.append({"x": x, "y": y, "text": c.get("text", "")})
    if others:
        best["also_at"] = others[:4]
    else:
        best.pop("also_at", None)
    return best


def _alnum(toks):
    return [t for t in toks if re.search(r"[a-z0-9]", t)]


def find_one(description, img, origin=(0, 0), words=None, exclude=(), control=None):
    """The best place in ONE set of OCR words, or None. Words inside `exclude`
    (Eva's own body, bubble and windows -- her bubble often shows the very words
    being looked for) are ignored."""
    variants = query_variants(description)
    if not variants or not query_words(description):
        return None                                  # only kind-words ("the button"): not text
    if control is None:
        control = is_control(description)
    if words and exclude:
        def outside(w):
            cx, cy = w[1] + w[3] / 2 + origin[0], w[2] + w[4] / 2 + origin[1]
            return not any(l <= cx <= r and t <= cy <= b for (l, t, r, b) in exclude)
        words = [w for w in words if outside(w)]
    if not words:
        return None
    lines = {}
    for w in words:
        lines.setdefault(w[5], []).append(w)
    cands = []
    for line in lines.values():
        line.sort(key=lambda w: w[1])
        toks = [(_tokens(w[0]) or [""])[0] for w in line]
        line_len = len(_alnum(toks))
        for q in variants:
            n = len(q)
            for size in range(max(1, n - 1), n + 3):
                for i in range(0, max(1, len(line) - size + 1)):
                    win = line[i:i + size]
                    sc = phrase_score(q, toks[i:i + size])
                    if sc <= 0:
                        continue
                    win_len = len(_alnum(toks[i:i + size]))
                    standalone = win_len >= line_len
                    rank = sc + (0.05 if standalone else 0.0)
                    if control and line_len > win_len + 1:
                        rank -= 0.15                  # a word inside running text is not the button
                    height = sorted(w[4] for w in win)[len(win) // 2]
                    cands.append((rank, sc, len(q), win, q, standalone, height))
    if not cands:
        return None
    cands.sort(key=lambda c: (-round(c[0], 2), -c[2], -c[6], len(c[3])))
    rank, best_s, _, best, q, standalone, height = cands[0]
    need = SHORT_THRESHOLD if (len(q) == 1 and len(q[0]) <= 5) else THRESHOLD
    if min(rank, best_s, 1.0) < need:                # bonuses rank candidates; they never lower the bar
        return None

    def box(win):
        x1 = min(w[1] for w in win) + origin[0]
        y1 = min(w[2] for w in win) + origin[1]
        x2 = max(w[1] + w[3] for w in win) + origin[0]
        y2 = max(w[2] + w[4] for w in win) + origin[1]
        return int(x1), int(y1), int(x2), int(y2)
    x1, y1, x2, y2 = box(best)
    out = {"found": True, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "center_x": (x1 + x2) // 2,
           "center_y": (y1 + y2) // 2, "score": round(min(best_s, 1.0), 3), "rank": rank,
           "standalone": standalone, "height": height, "text": " ".join(w[0] for w in best)}
    # Other places that read just as well (two "Play" buttons): say so, do not pick silently.
    others = []
    for c in cands[1:]:
        if c[0] < rank - 0.02:
            continue
        win = c[3]
        if set(map(id, win)) & set(map(id, best)):
            continue                                    # the same words again
        bx = box(win)
        cx, cy = (bx[0] + bx[2]) // 2, (bx[1] + bx[3]) // 2
        if abs(cx - out["center_x"]) > 40 or abs(cy - out["center_y"]) > 25:
            if all(abs(cx - o["x"]) > 40 or abs(cy - o["y"]) > 25 for o in others):
                others.append({"x": cx, "y": cy, "text": " ".join(w[0] for w in win)})
    if others:
        out["also_at"] = others[:4]
    return out
