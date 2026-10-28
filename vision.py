"""
Eva -- visual perception: finding things on screen and answering questions about it.

Order of looking (locate_on_screen), fastest and most exact first:
  0. the STRUCTURE the window publishes (structure.py, UI Automation: every
     app and every browser page; exact names, roles and rectangles);
  1. the WORDS on screen, read by the PC's own OCR (screen_text.py, < 1 s,
     exact rectangles; forgiving of misheard words);
  2. the vision model, coarse on the whole screen (or the area given), then
     refined on a native-resolution crop around the first answer.
The accessibility tree (ui_tree) and the browser's DOM come before both,
in the tools that use them.

COORDINATES -- THE ROOT CAUSE OF "SHE CANNOT FIND THINGS" (eva.log 2026-10-11
19:20-20:08). The vision model answers in coordinates NORMALISED TO 0-1000,
the Qwen-VL / grounding convention, even when asked for pixels. On a
1536 x 864 image those numbers usually fit inside the image, so they were
taken as pixels: "man holding deer" came back as (798, 790) -> clicked at
(985, 984); the real thumbnail was at (1533, 853) = 798/1000 x 1920,
790/1000 x 1080. Every target was pulled toward the top-left. Now the prompt
ASKS for 0-1000, and a one-time calibration per model (a synthetic picture
with a target at a known place, ~5 s, cached in ~/.eva/vision_calibration.json)
measures which convention the model really uses: 0-1000, 0-1000 with y first
(Gemma), 0-1 or pixels. config.VISION_COORDS forces one.

HANGS (same log: 3 abandoned at the 90 s deadline, 27 "still running"). The
OpenAI client silently retried 503s on its own (max_retries=2) on top of
Eva's own retries, and one 503 took 91 s to arrive. Now: no hidden retries,
VISION_REQUEST_TIMEOUT (25 s) per request, one retry while busy, then the next
model in VISION_FALLBACK_MODELS; a model that answers 404/410 is skipped for
the session. Reasoning ("thinking") is switched off for these calls where the
model accepts it: locating a button does not need deliberation.
"""
import base64
import collections
import hashlib
import io
import json
import os
import re
import threading
import time

import providers

import config
import desktop_control as dc
from logger import get_logger

log = get_logger(__name__)

REQUEST_TIMEOUT = float(getattr(config, "VISION_REQUEST_TIMEOUT", 25))
LOCATE_BUDGET = float(getattr(config, "VISION_LOCATE_BUDGET", 60))
_client = None        # the checks put a scripted client here; Eva uses providers.client per model
# Each vision model may be 'provider:model' (providers.py); no hidden retries (max_retries=0).

# The model accepts up to a 12-tile / 512px layout (about 2048x1536).
# A 1920x1080 screen already fits, but downscaling cuts upload size and
# latency noticeably; _scale records the factor so coordinates the model
# reports against the resized image can be mapped back to real screen
# pixels.
_MAX_W = 1536


def _encode(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---- answer cache ------------------------------------------------------------------------
# The same question about the same screen gets the same answer without another
# vision call. Reused ONLY while the screen is unchanged: the key includes a
# fingerprint of the exact pixels (1/4 size, 5-bit grey) with Eva's own windows
# blanked out (she is always moving). 120 s, 48 entries.
CACHE_SECONDS = 120
CACHE_MAX = 48
SELF_RECTS = []              # [(left, top, right, bottom)] of Eva's own windows, kept by main.py (GUI thread)
AVATAR_RECT = [None]         # her body's rectangle, for "near me" / "above your head" (main.py)
_cache = collections.OrderedDict()
_cache_lock = threading.Lock()
_tls = threading.local()
CACHE_STATS = {"hits": 0, "misses": 0}


def _fingerprint(img, origin):
    """Hash of the picture with Eva's own windows blanked out."""
    small = img.convert("L").resize((max(1, img.width // 4), max(1, img.height // 4))).point(lambda v: v & 0xF8)
    px = bytearray(small.tobytes())
    w, h = small.size
    for (l, t, r, b) in list(SELF_RECTS):
        x0, y0 = max(0, (l - origin[0]) // 4), max(0, (t - origin[1]) // 4)
        x1, y1 = min(w, (r - origin[0]) // 4 + 1), min(h, (b - origin[1]) // 4 + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        for y in range(y0, y1):
            px[y * w + x0:y * w + x1] = bytes(x1 - x0)
    return hashlib.blake2b(bytes(px) + f"{w}x{h}@{origin}".encode(), digest_size=16).hexdigest()


def _cache_key(kind, text, crop):
    fp = getattr(_tls, "fp", None)
    if not fp:
        return None
    return (kind, " ".join(str(text or "").lower().split()), crop, fp)


def _cache_get(key):
    if key is None:
        return None
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] <= CACHE_SECONDS:
            _cache.move_to_end(key)
            CACHE_STATS["hits"] += 1
            return dict(hit[1], cached=True)
        _cache.pop(key, None)
        CACHE_STATS["misses"] += 1
    return None


def _cache_put(key, value):
    if key is None:
        return
    with _cache_lock:
        _cache[key] = (time.monotonic(), dict(value))
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def clear_cache():
    with _cache_lock:
        _cache.clear()


# ---- capture -----------------------------------------------------------------------------
def _capture(crop=None):
    """(PIL image in SCREEN pixels, origin) of the screen or a part of it, or (None, origin)."""
    _tls.fp = None
    _tls.img = None
    shot = dc.screenshot()
    path = shot.get("path") if isinstance(shot, dict) else None
    if not path:
        return None, (0, 0)
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        # mss captures the VIRTUAL desktop, whose top-left is not (0,0) when a
        # monitor sits left of or above the primary one.
        origin = (shot.get("origin_x", 0), shot.get("origin_y", 0))
        if crop:
            left, top, right, bottom = crop
            left = max(0, left - origin[0]); top = max(0, top - origin[1])
            right = min(img.width, right - origin[0]); bottom = min(img.height, bottom - origin[1])
            if right - left < 16 or bottom - top < 16:
                return None, origin
            img = img.crop((left, top, right, bottom))
            origin = (origin[0] + left, origin[1] + top)
        try:
            _tls.fp = _fingerprint(img, origin)
        except Exception:
            _tls.fp = None
        _tls.img = img
        return img, origin
    except Exception:
        log.exception("Could not prepare the screenshot")
        return None, (0, 0)


def _mask_own_windows(img, origin):
    """The picture sent to the vision model, with Eva's own windows (body, bubble, chat, dialogs)
    painted over in neutral grey. 2026-10-17 15:47 (welcome tour, "what is on my screen?"): she
    described her own chat window, and the welcome promised these are masked -- they were masked
    only in the screen fingerprint, not in the picture that leaves the PC. A copy: OCR and the
    cache keep the real pixels (they exclude these areas themselves)."""
    rects = list(SELF_RECTS)
    if not rects or not getattr(config, "VISION_MASK_OWN_WINDOWS", True):
        return img
    try:
        from PIL import ImageDraw
        out = img.copy()
        draw = ImageDraw.Draw(out)
        for (l, t, r, b) in rects:
            box = (l - origin[0], t - origin[1], r - origin[0], b - origin[1])
            if box[2] <= 0 or box[3] <= 0 or box[0] >= out.width or box[1] >= out.height:
                continue
            draw.rectangle(box, fill=(128, 128, 128))
        return out
    except Exception:
        log.exception("Could not mask Eva's own windows -- sending the picture as captured")
        return img


def _grab_screen(crop=None):
    """(base64 PNG for the model, scale, origin, (w, h) of the image sent)."""
    img, origin = _capture(crop)
    if img is None:
        return None, 1.0, origin, (0, 0)
    try:
        img = _mask_own_windows(img, origin)
        scale = 1.0
        if img.width > _MAX_W:
            scale = _MAX_W / img.width
            img = img.resize((_MAX_W, int(img.height * scale)))
        return _encode(img), scale, origin, (img.width, img.height)
    except Exception:
        log.exception("Could not prepare the screenshot for the vision model")
        return None, 1.0, (0, 0), (0, 0)


# ---- asking the model --------------------------------------------------------------------
_UNAVAILABLE = set()                   # models that answered 404 / 410 this session
_NO_THINK = {}                         # model -> False once it refused the "thinking off" switch


def models():
    out = [config.VISION_MODEL_ID] + list(getattr(config, "VISION_FALLBACK_MODELS", ()) or ())
    seen, keep = set(), []
    for m in out:
        if m and m not in seen and m not in _UNAVAILABLE and (_client is not None or providers.usable(m)[0]):
            seen.add(m)
            keep.append(m)
    return keep


def _busy_error(e) -> bool:
    m = str(e)
    return any(k in m for k in ("503", "429", "ResourceExhausted", "request limit", "Too Many Requests",
                                "Service Unavailable", "overloaded", "timed out", "Timeout", "timeout"))


def _gone_error(e) -> bool:
    m = str(e).lower()
    return "410" in m or "404" in m or "end of life" in m or "no longer available" in m or "not found" in m


def _param_error(e) -> bool:
    m = str(e).lower()
    return ("400" in m or "422" in m) and ("chat_template" in m or "enable_thinking" in m or "extra" in m
                                           or "unrecognized" in m or "unexpected" in m)


def _ask(prompt: str, b64: str, max_tokens: int = 1024, deadline: float = None, only_model: str = None,
         think: bool = False):
    """The answer text from the first vision model that gives one in time.
    Sets _tls.model (whose coordinate convention applies)."""
    deadline = deadline or (time.monotonic() + LOCATE_BUDGET)
    last = None
    for model in ([only_model] if only_model else models()):
        for attempt in range(2):
            left = deadline - time.monotonic()
            if left < 3:
                break
            # NVIDIA's thinking switch, only where it is understood (strict providers reject it)
            extra = ({"chat_template_kwargs": {"enable_thinking": False}}
                     if not think and _NO_THINK.get(model, True) and (providers.is_nim(model) or _client is not None)
                     else None)
            try:
                # With thinking ON, reasoning tokens count against max_tokens (NVIDIA NIM docs for
                # Nemotron 3.5 Super VL): a 300-token budget would come back empty.
                budget = max_tokens if extra else max(max_tokens, 4096)
                kw = dict(model=model, max_tokens=budget, temperature=0.1, timeout=min(REQUEST_TIMEOUT, left),
                          messages=[{"role": "user", "content": [
                              {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                              {"type": "text", "text": prompt}]}])
                if extra:
                    kw["extra_body"] = extra
                kw["model"] = providers.parse(model)[1]
                if _client is None:
                    providers.pace(model)
                resp = (_client or providers.client(model, timeout=REQUEST_TIMEOUT, max_retries=0)
                        ).chat.completions.create(**kw)
                _tls.model = model
                text = (resp.choices[0].message.content or "").strip()
                return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
            except Exception as e:
                last = e
                if extra and _param_error(e):
                    _NO_THINK[model] = False                    # this model wants its thinking: ask again without
                    continue
                if _gone_error(e):
                    _UNAVAILABLE.add(model)
                    log.error(f"Vision model {model} is not available ({str(e)[:100]}) -- "
                              + ("trying the next one" if models() else "no vision model left"))
                    break
                if _busy_error(e):
                    if attempt == 0 and deadline - time.monotonic() > 10:
                        log.info(f"Vision service busy ({str(e)[:80]}) -- retrying in 1.5 s")
                        time.sleep(1.5)
                        continue
                    log.info(f"Vision model {model} still busy -- trying the next model")
                    break
                raise
    if last is not None:
        raise last
    raise TimeoutError("no vision model answered in time")


def _vision_error(e) -> dict:
    """A retired model answers 410 Gone forever -- a configuration fault, not a
    transient failure, and must not read like one."""
    msg = str(e)
    if _gone_error(e) and not models():
        log.error(f"VISION MODEL UNAVAILABLE -- {config.VISION_MODEL_ID} is retired. "
                  f"Eva cannot see the screen until VISION_MODEL_ID in config.py is changed.")
        return {"error": f"my vision model ({config.VISION_MODEL_ID}) has been retired and "
                         "no longer exists, so I CANNOT see the screen at all. Do not guess "
                         "at what is on screen -- say you cannot look, and use "
                         "read_window_text or ui_tree on a specific window instead."}
    if _busy_error(e):
        return {"error": "the vision service is busy right now and did not answer in time. Say so; try once more "
                         "in a moment, or use ui_tree / read_window_text, or ask the person where it is.",
                "busy": True}
    return {"error": msg}


# ---- coordinate conventions -----------------------------------------------------------------
CONVENTIONS = ("norm1000", "norm1000_yx", "norm1", "pixels")
CALIBRATION_PATH = os.path.join(os.path.expanduser("~"), ".eva", "vision_calibration.json")
_calib = {"data": None, "running": set()}
_calib_lock = threading.Lock()


def _calibration():
    if _calib["data"] is None:
        try:
            with open(CALIBRATION_PATH, encoding="utf-8") as f:
                _calib["data"] = json.load(f)
        except (OSError, ValueError):
            _calib["data"] = {}
    return _calib["data"]


def convention(model=None):
    forced = str(getattr(config, "VISION_COORDS", "auto") or "auto").lower()
    if forced in CONVENTIONS:
        return forced
    model = model or getattr(_tls, "model", None) or config.VISION_MODEL_ID
    c = _calibration().get(model, {}).get("convention")
    return c if c in CONVENTIONS else "norm1000"         # what this family of models does (see the log above)


def to_image(x1, y1, x2, y2, w, h, conv):
    """A box as the model gave it -> pixels of the image it saw."""
    if conv == "norm1000_yx":
        x1, y1, x2, y2 = y1, x1, y2, x2
        conv = "norm1000"
    if conv == "norm1000":
        if max(x1, y1, x2, y2) <= 1.0:
            conv = "norm1"                               # it answered 0-1 this time
        elif max(x1, x2) > 1000 or max(y1, y2) > 1000:
            conv = "pixels"                              # clearly pixels this time
        else:
            return x1 * w / 1000, y1 * h / 1000, x2 * w / 1000, y2 * h / 1000
    if conv == "norm1":
        return x1 * w, y1 * h, x2 * w, y2 * h
    # pixels -- unless the numbers cannot be pixels of this image
    if (max(x1, x2) > w + 2 or max(y1, y2) > h + 2) and max(x1, x2, y1, y2) <= 1000:
        log.warning("Vision returned normalised (0-1000) coordinates -- converting.")
        return x1 * w / 1000, y1 * h / 1000, x2 * w / 1000, y2 * h / 1000
    return x1, y1, x2, y2


def _calibration_picture():
    """1536 x 864, light page with some UI-like boxes; the target is a red disc
    with a white 7 at a place where every convention gives a different answer."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1536, 864), (236, 238, 242))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1536, 56), fill=(52, 58, 70))
    for i, x in enumerate(range(40, 900, 210)):
        d.rectangle((x, 120, x + 170, 300), fill=(255, 255, 255), outline=(200, 200, 205))
        d.rectangle((x + 10, 320 + (i % 2) * 20, x + 160, 340 + (i % 2) * 20), fill=(205, 208, 214))
    d.rectangle((260, 600, 340, 680), fill=(40, 110, 220))                  # a blue square (distractor)
    d.ellipse((1156, 171, 1224, 239), fill=(220, 30, 40))                    # the target, centre (1190, 205)
    d.text((1184, 196), "7", fill=(255, 255, 255))
    return img, (1190, 205)


def calibrate(model):
    """Measure which coordinate convention `model` answers in. Returns it, or None."""
    img, (tx, ty) = _calibration_picture()
    b64 = _encode(img)
    raw = _ask(_locate_prompt("a red circle with the number 7 in it", img.width, img.height), b64,
               max_tokens=200, deadline=time.monotonic() + 45, only_model=model)
    data = parse_box_reply(raw)
    if not isinstance(data, dict) or not data.get("found"):
        log.info(f"Vision calibration of {model}: no usable answer ({str(raw)[:120]})")
        return None
    x1, y1, x2, y2 = (float(data[k]) for k in ("x1", "y1", "x2", "y2"))
    best, err = None, 1e9
    for conv in CONVENTIONS:
        if conv == "norm1000_yx":
            bx = [y1 * img.width / 1000, x1 * img.height / 1000, y2 * img.width / 1000, x2 * img.height / 1000]
        elif conv == "norm1000":
            bx = [x1 * img.width / 1000, y1 * img.height / 1000, x2 * img.width / 1000, y2 * img.height / 1000]
        elif conv == "norm1":
            bx = [x1 * img.width, y1 * img.height, x2 * img.width, y2 * img.height]
        else:
            bx = [x1, y1, x2, y2]
        e = ((bx[0] + bx[2]) / 2 - tx) ** 2 + ((bx[1] + bx[3]) / 2 - ty) ** 2
        if e < err:
            best, err = conv, e
    err = err ** 0.5
    ok = err <= 70
    with _calib_lock:
        _calibration()[model] = {"convention": best if ok else None, "error_px": round(err), "at": time.time(),
                                 "raw": str(raw)[:200]}
        try:
            os.makedirs(os.path.dirname(CALIBRATION_PATH), exist_ok=True)
            with open(CALIBRATION_PATH, "w", encoding="utf-8") as f:
                json.dump(_calibration(), f, indent=1)
        except OSError:
            pass
    log.info(f"Vision calibration of {model}: answers in {best} (off by {err:.0f} px)"
             + ("" if ok else " -- too far off to trust; it is used for questions only"))
    return best if ok else None


def calibrate_in_background(model=None):
    model = model or config.VISION_MODEL_ID
    with _calib_lock:
        if model in _calib["running"] or (_calibration().get(model, {}).get("convention") in CONVENTIONS):
            return False
        _calib["running"].add(model)

    def run():
        try:
            calibrate(model)
        except Exception as e:
            log.info(f"Vision calibration of {model} did not run ({str(e)[:100]}); trying again later")
        finally:
            with _calib_lock:
                _calib["running"].discard(model)
    threading.Thread(target=run, name="eva-vision-calibrate", daemon=True).start()
    return True


# ---- reading the model's answer -------------------------------------------------------------
_BOX_ONLY = re.compile(r"^\s*(?:<box>.*</box>|<point>.*</point>|\[\[?[\d.,\s()]+\]?\])\s*$", re.DOTALL)
_BOX_TAG = re.compile(r"<box>\s*\[\[?\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\]\]?\s*</box>")
_POINT_TAG = re.compile(r"<point>\s*\[?\s*\(?\s*([\d.]+)\s*,\s*([\d.]+)\s*\)?\s*\]?\s*</point>")


def parse_box_reply(raw):
    """The vision model's box as {"found", "x1".."y2", ...} -- from the JSON asked
    for ({"box": [..]} or x1..y2), or the grounding formats models answer in
    anyway: [{"bbox_2d": [...]}], <box>[[...]]</box>, <point>[(x, y)]</point>.
    None when there is nothing usable."""
    raw = str(raw or "").strip()
    tag = _BOX_TAG.search(raw)
    if tag:
        x1, y1, x2, y2 = (float(v) for v in tag.groups())
        return {"found": True, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "what": ""}
    pt = _POINT_TAG.search(raw)
    if pt:
        x, y = (float(v) for v in pt.groups())
        return {"found": True, "x1": x, "y1": y, "x2": x, "y2": y, "what": "", "point": True}
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    data = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]|\{.*\}", raw, re.DOTALL)  # models often wrap JSON in prose
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = None
    if isinstance(data, list):
        if len(data) == 4 and all(isinstance(v, (int, float)) for v in data):
            data = {"found": True, "box": data}
        else:
            data = next((d for d in data if isinstance(d, dict)), None)
    if isinstance(data, dict):
        box = data.get("bbox_2d") or data.get("bbox") or data.get("box")
        if isinstance(box, (list, tuple)) and len(box) == 4 and "x1" not in data:
            try:
                x1, y1, x2, y2 = (float(v) for v in box)
            except (TypeError, ValueError):
                return None
            return {"found": data.get("found", True) is not False, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                    "what": str(data.get("label") or data.get("what") or "")}
        point = data.get("point")
        if isinstance(point, (list, tuple)) and len(point) == 2 and "x1" not in data:
            x, y = (float(v) for v in point)
            return {"found": True, "x1": x, "y1": y, "x2": x, "y2": y, "what": str(data.get("what") or ""),
                    "point": True}
    return data


def _locate_prompt(description, w, h):
    return (
        f"Find this in the image: {description}\n"
        "Reply with ONLY a JSON object and nothing else.\n"
        'If you can see it: {"found": true, "box": [x1, y1, x2, y2], "what": "<what you found, a few words>"}\n'
        "The box uses coordinates NORMALISED TO 0-1000 on both axes: (0, 0) is the top-left corner of the "
        "image and (1000, 1000) the bottom-right corner, whatever the image size. x1, y1 is the top-left of "
        "the thing, x2, y2 its bottom-right; x is horizontal, y vertical.\n"
        'If you genuinely cannot see it: {"found": false, "reason": "<why>"}\n'
        "Do not guess a place you cannot see -- a wrong box causes a misplaced click.")


def _locate_in(b64, description, scale, origin, dims, conv=None, deadline=None):
    """One localisation pass over one image. Returns a screen-space box."""
    w, h = dims
    raw = _ask(_locate_prompt(description, w, h), b64, max_tokens=300, deadline=deadline)
    data = parse_box_reply(raw)
    if not isinstance(data, dict):
        return {"found": False, "reason": f"could not read the vision model's reply: {str(raw)[:200]}"}
    if not data.get("found"):
        return {"found": False, "reason": data.get("reason", "not visible on screen")}
    try:
        x1, y1, x2, y2 = (float(data[k]) for k in ("x1", "y1", "x2", "y2"))
    except (KeyError, TypeError, ValueError):
        return {"found": False, "reason": "vision model returned no usable box"}
    x1, y1, x2, y2 = to_image(x1, y1, x2, y2, w, h, conv or convention())
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    if data.get("point"):                                   # a point: a small box around it
        x1, y1, x2, y2 = x1 - 6, y1 - 6, x2 + 6, y2 + 6
    if (x2 - x1) < 2 or (y2 - y1) < 2:
        return {"found": False, "reason": "vision returned a degenerate (zero-area) box"}
    if x1 < -4 or y1 < -4 or x2 > w + 4 or y2 > h + 4:
        return {"found": False, "reason": f"vision returned a box outside the image ({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f})"}

    to_screen = lambda v, o: int(v / scale) + o
    return {"found": True,
            "x1": to_screen(max(0, x1), origin[0]), "y1": to_screen(max(0, y1), origin[1]),
            "x2": to_screen(min(w, x2), origin[0]), "y2": to_screen(min(h, y2), origin[1]),
            "what": data.get("what") or description}


# ---- where to look ----------------------------------------------------------------------------
AREAS = ["anywhere", "near_me", "left_half", "right_half", "top_half", "bottom_half"]


def search_area(area="anywhere", left=None, top=None, right=None, bottom=None):
    """The part of the screen to search, in screen pixels, or None for all of it."""
    if None not in (left, top, right, bottom):
        return int(left), int(top), int(right), int(bottom)
    area = str(area or "anywhere").lower()
    if area == "anywhere":
        return None
    try:
        v = dc.get_screen_size().get("virtual_screen") or {}
        L, T = v.get("left", 0), v.get("top", 0)
        R, B = L + v.get("width", 1920), T + v.get("height", 1080)
    except Exception:
        L, T, R, B = 0, 0, 1920, 1080
    if area == "near_me" and AVATAR_RECT[0]:
        l, t, r, b = AVATAR_RECT[0]
        return max(L, l - 520), max(T, t - 620), min(R, r + 520), min(B, b + 160)
    halves = {"left_half": (L, T, (L + R) // 2 + 60, B), "right_half": ((L + R) // 2 - 60, T, R, B),
              "top_half": (L, T, R, (T + B) // 2 + 60), "bottom_half": (L, (T + B) // 2 - 60, R, B)}
    return halves.get(area)


# ---- the tools ----------------------------------------------------------------------------------
def look_at_screen(question: str, left: int = None, top: int = None, right: int = None, bottom: int = None):
    """Answer a question about what is currently on screen -- or, given a box
    (screen pixels), about just that part of it."""
    crop = None
    if None not in (left, top, right, bottom):
        crop = (int(left), int(top), int(right), int(bottom))
    b64, scale, origin, dims = _grab_screen(crop)
    if not b64 and crop:
        return {"error": "that area is too small or off the screen"}
    if not b64:
        return {"error": "could not capture the screen"}
    key = _cache_key("look", question, crop)
    hit = _cache_get(key)
    if hit:
        log.info(f"Vision answer (screen unchanged, cached): {hit.get('answer', '')[:120]!r}")
        return hit
    try:
        what = "this part of a Windows screen" if crop else "this screenshot of a Windows desktop"
        answer = _ask(f"Look at {what} and answer: {question}\n"
                      f"Answer concisely, in plain words, only from what is actually visible.", b64)
        out = {"answer": answer}
        if _BOX_ONLY.match(answer or ""):
            # eva.log 2026-10-11 19:25 / 20:05: the answer was only "<point>[(880, 720)]</point>" --
            # a position in 0-1000 units that she then clicked as screen pixels. Turn it into
            # real screen coordinates, and ask again for words.
            box = parse_box_reply(answer)
            if isinstance(box, dict) and box.get("found"):
                x1, y1, x2, y2 = to_image(box["x1"], box["y1"], box["x2"], box["y2"], dims[0], dims[1], convention())
                cx, cy = int((x1 + x2) / 2 / scale) + origin[0], int((y1 + y2) / 2 / scale) + origin[1]
                out["point"] = {"x": cx, "y": cy}
            try:
                words = _ask(f"Look at {what} and answer in plain words (no coordinates, no boxes): {question}", b64)
            except Exception:
                words = ""
            out["answer"] = (words or "") + (f" (on screen at about x={out['point']['x']}, y={out['point']['y']})"
                                             if "point" in out else "")
        log.info(f"Vision answer: {out['answer'][:200]!r}")
        if out["answer"]:
            _cache_put(key, out)
        return out
    except Exception as e:
        return _vision_error(e)


def _text_again(description, region):
    try:
        import screen_text
        img, origin = _capture(region)
        if img is None:
            return None
        txt = screen_text.find_text(description, img, origin, exclude=list(SELF_RECTS), key=getattr(_tls, "fp", None))
    except Exception:
        return None
    if not txt:
        return None
    log.info(f"Located {description!r} by its text {txt['text']!r} at {txt['center_x']},{txt['center_y']} "
             "(second look, after the vision service failed)")
    return {"found": True, "center_x": txt["center_x"], "center_y": txt["center_y"],
            "box": {k: txt[k] for k in ("x1", "y1", "x2", "y2")}, "what": txt["text"], "via": "text on screen",
            "refined": True, "match": txt["score"]}


def locate_on_screen(description: str, area: str = "anywhere", left: int = None, top: int = None,
                     right: int = None, bottom: int = None):
    """Find something on screen and return real screen coordinates for it.

    1. Words on screen (local OCR, screen_text.py): exact, < 1 s, forgiving of
       misheard words. 2. The vision model: coarse on the whole search area,
       then refined on a native-resolution crop around the first answer.
    `area` narrows the search ("near_me" for "above your head", "right_half",
    ...), which is both faster and more accurate."""
    deadline = time.monotonic() + LOCATE_BUDGET
    region = search_area(area, left, top, right, bottom)
    img, origin = _capture(region)
    if img is None:
        return {"error": "could not capture the screen" if not region else "that area is too small or off the screen"}
    key = _cache_key("locate", description, region)
    hit = _cache_get(key)
    if hit:
        log.info(f"Vision located {description!r} at {hit['center_x']},{hit['center_y']} (screen unchanged, cached)")
        return hit

    # 0. the structure the window publishes (UI Automation: names, roles, exact rectangles)
    try:
        import structure
        hit = structure.locate(description, region, exclude=list(SELF_RECTS), key=getattr(_tls, "fp", None))
        if hit:
            result = {"found": True, "center_x": hit["center_x"], "center_y": hit["center_y"],
                      "box": {k: hit[k] for k in ("x1", "y1", "x2", "y2")}, "what": hit["text"],
                      "role": hit["role"], "via": "window structure", "refined": True, "match": hit["score"],
                      "window_handle": hit["window_handle"]}
            if not hit.get("enabled", True):
                result["note"] = "this control is disabled (greyed out) right now: something must be done first"
            if hit.get("also_at"):
                result["also_at"] = hit["also_at"]
                result.setdefault("note", "the same name is in more than one place (also_at); the one given is "
                                          "the most likely control -- if clicking it does not do it, try the next")
            _cache_put(key, result)
            return result
    except Exception:
        log.exception("Structure search failed -- reading the screen's text")

    # 1. the words on screen
    try:
        import screen_text
        t0 = time.monotonic()
        txt = screen_text.find_text(description, img, origin, exclude=list(SELF_RECTS),
                                    key=getattr(_tls, "fp", None))
        if txt:
            result = {"found": True, "center_x": txt["center_x"], "center_y": txt["center_y"],
                      "box": {k: txt[k] for k in ("x1", "y1", "x2", "y2")}, "what": txt["text"],
                      "via": "text on screen", "refined": True, "match": txt["score"]}
            if txt.get("also_at"):
                result["also_at"] = txt["also_at"]
                result["note"] = ("the same words are in more than one place (also_at). The one given is the "
                                  "most button-like (a label on its own, the largest text); if clicking it does "
                                  "not do what was asked, try the next one, or ask which")
            log.info(f"Located {description!r} by its text {txt['text']!r} at {txt['center_x']},{txt['center_y']} "
                     f"({(time.monotonic() - t0) * 1000:.0f} ms, match {txt['score']})")
            _cache_put(key, result)
            return result
    except Exception:
        log.exception("Text search on screen failed -- looking with the vision model")

    # 2. the vision model
    if convention() == "norm1000" and str(getattr(config, "VISION_COORDS", "auto")).lower() == "auto":
        calibrate_in_background()               # measured once per model; until then the usual convention
    try:
        scale = 1.0
        send = img
        if img.width > _MAX_W:
            scale = _MAX_W / img.width
            send = img.resize((_MAX_W, int(img.height * scale)))
        coarse = _locate_in(_encode(send), description, scale, origin, (send.width, send.height), deadline=deadline)
    except Exception as e:
        # eva.log 2026-10-11 23:01: the vision service was busy for 56 s while the "Scan"
        # button had appeared meanwhile -- the next look found it by its text in 94 ms.
        # Before giving up, read the screen's words once more.
        again = _text_again(description, region)
        return again or _vision_error(e)
    if not coarse.get("found"):
        return {"found": False, "reason": coarse.get("reason")}

    # Re-look at native resolution around the first answer: the target is several times larger there.
    bw, bh = coarse["x2"] - coarse["x1"], coarse["y2"] - coarse["y1"]
    pad = max(140, bw, bh)
    crop = (coarse["x1"] - pad, coarse["y1"] - pad, coarse["x2"] + pad, coarse["y2"] + pad)
    refined = None
    if deadline - time.monotonic() > 6:
        b64b, scaleb, originb, dimsb = _grab_screen(crop=crop)
        if b64b:
            try:
                candidate = _locate_in(b64b, description, scaleb, originb, dimsb, deadline=deadline)
                if candidate.get("found"):
                    refined = candidate
            except Exception:
                log.info("Refinement pass failed -- using the first estimate")

    best = refined or coarse
    result = {"found": True,
              "center_x": (best["x1"] + best["x2"]) // 2,
              "center_y": (best["y1"] + best["y2"]) // 2,
              "box": {k: best[k] for k in ("x1", "y1", "x2", "y2")},
              "what": best.get("what", description), "via": "vision",
              "refined": refined is not None}
    if refined is None:
        result["note"] = ("only a single coarse pass succeeded, so this position is less "
                          "reliable -- check the result of the click rather than assuming it "
                          "landed correctly")
    log.info(f"Vision located {description!r} at {result['center_x']},{result['center_y']} "
             f"(refined={result['refined']}, {convention()})")
    if result["refined"]:
        _cache_put(key, result)
    return result
