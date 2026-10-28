"""Eva -- the person's own settings, kept outside config.py.

config.py holds the defaults; ~/.eva/settings.json holds what the person chose
in the Settings window (tray > Settings) or asked her to change ("talk less",
"make the bubbles green"). At start-up config.py calls apply(), so every
module sees the chosen values; a change made while she runs is applied to the
config module at once, and the window says which ones wait for a restart.

Only the settings listed in SETTINGS can be set this way. Anything that
decides safety -- what asks first (CONFIRM_EFFECTS), the API key, command
limits -- is NOT here, so neither the file nor a request can loosen it.
"""
import json
import os
import threading

PATH = os.path.join(os.path.expanduser("~"), ".eva", "settings.json")


def _s(key, label, kind, group, live=True, choices=None, lo=None, hi=None, step=None, help=""):
    return {"key": key, "label": label, "kind": kind, "group": group, "live": live, "choices": choices,
            "min": lo, "max": hi, "step": step, "help": help}


VOICES = ["en-US-AriaNeural", "en-US-JennyNeural", "en-US-AnaNeural", "en-IN-NeerjaNeural",
          "en-GB-SoniaNeural", "en-AU-NatashaNeural", "en-US-GuyNeural"]

SETTINGS = [
    # Look and movement
    _s("AVATAR_SCALE", "Size", "float", "Look and movement", live=False, lo=0.35, hi=1.2, step=0.05,
       help="1.0 = 200 x 380 pixels"),
    _s("ANIMATION_SPEED", "Gesture speed", "float", "Look and movement", lo=0.5, hi=1.6, step=0.1,
       help="1.0 = normal; lower is calmer, higher is snappier"),
    _s("EMBODIMENT_MODE", "Acting out her work", "choice", "Look and movement", live=False,
       choices=["full", "brisk", "off"], help="full: walks, reaches and reacts; brisk: reacts only to problems"),
    _s("PROCEDURAL_GESTURES", "Hand gestures", "bool", "Look and movement"),
    _s("IDLE_LIFE", "Her own life when left alone", "bool", "Look and movement",
       help="sits on windows, looks around, dozes"),
    _s("PRESENCE", "Eye contact and small movements", "bool", "Look and movement",
       help="looks at you and your cursor, nods when you speak, fidgets when bored"),
    _s("BUBBLE_COLOURS", "Bubble colours", "choice", "Look and movement", choices=["blue", "green", "grey"]),
    # Speech
    _s("TTS_VOICE", "Voice", "choice", "Speech", choices=VOICES),
    _s("LONG_REPLY_CHARS", "Say aloud up to (characters)", "int", "Speech", lo=40, hi=2000, step=20,
       help="longer replies go to the bubble and the chat window"),
    _s("LONG_REPLY_TARGET", "Long replies go to", "choice", "Speech", choices=["bubble", "notepad"]),
    _s("BARGE_IN", "Talking over her stops her", "bool", "Speech"),
    _s("TTS_BACKEND", "Her voice service", "choice", "Speech", choices=["nvidia", "edge"],
       help="nvidia: NVIDIA Magpie with your model key (starts speaking sooner); edge: Microsoft Edge. "
            "Either falls back to the other, then to the Windows voice"),
    _s("TTS_NVIDIA_VOICE", "NVIDIA voice", "choice", "Speech",
       choices=["Magpie-Multilingual.EN-US.Aria", "Magpie-Multilingual.EN-US.Mia", "Magpie-Multilingual.EN-US.Jason",
                "Magpie-Multilingual.EN-US.Mia.Calm"]),
    _s("STT_INPUT_DEVICE", "Microphone", "choice", "Speech", live=False,
       choices=["auto", "Headset", "Microphone Array"],
       help="auto: earbuds or a headset when connected, else the Windows default"),
    _s("STT_BACKEND", "Speech recognition", "choice", "Speech", choices=["google", "nvidia", "phonon"],
       help="nvidia: NVIDIA's free recognisers with your model key; phonon: on this PC (start_phonon.bat); "
            "both fall back to google"),
    _s("STT_NVIDIA_MODEL", "NVIDIA recogniser", "choice", "Speech",
       choices=["parakeet-tdt-0.6b-v2", "whisper-large-v3", "canary-1b"]),
    # Thinking
    _s("REASONING_EFFORT", "How long she thinks", "choice", "Thinking", choices=["low", "medium", "high"]),
    _s("LEARN_SKILLS", "Learn skills from tasks (you approve each)", "bool", "Thinking", live=False),
    _s("HELPER_MAX", "Background helpers at once", "int", "Thinking", live=False, lo=1, hi=6, step=1),
    # Initiative
    _s("PREDICTIVE_HELP", "Offer my usual routines", "bool", "Initiative",
       help="learns on this PC what you open at which time; only offers, never acts without your yes; "
            "never anything to do with money"),
    _s("PREDICTIVE_MIN_SEEN", "Offer after seeing it (days)", "int", "Initiative", lo=2, hi=10, step=1),
    _s("PATROL", "Look around the PC when bored and offer fixes", "bool", "Initiative",
       help="full disk, old downloads, memory or CPU hogs, a big Recycle Bin; only offers, never acts without your yes"),
    _s("PROACTIVE_SUGGESTIONS", "Tidy-up hints (many tabs, full disk)", "bool", "Initiative"),
    _s("PROACTIVE_COMMENTARY_ENABLED", "Remarks about the window in front", "bool", "Initiative"),
    # Privacy
    _s("REPLAY_FAILED_TASKS", "Keep a replay of tasks that went wrong", "bool", "Privacy",
       help="the steps of a failed task, on this PC only (tray > Failed tasks)"),
    _s("STT_KEEP_PHRASES", "Keep the last phrases she heard (to check her hearing)", "int", "Privacy", lo=0, hi=50,
       step=5, help="as sound files on this PC only (.eva\\hearing); 0 keeps none"),
    _s("REPLAY_SCREENSHOTS", "... with a picture after each on-screen step", "bool", "Privacy",
       help="never after a step that handles passwords or payments"),
    # Hotkeys
    _s("HOTKEY_CHAT", "Open chat", "hotkey", "Hotkeys", live=False),
    _s("HOTKEY_REGION", "Ask about part of the screen", "hotkey", "Hotkeys", live=False),
    _s("HOTKEY_STOP", "Stop everything", "hotkey", "Hotkeys", live=False),
]
BY_KEY = {s["key"]: s for s in SETTINGS}
DEFAULTS = {}                 # config.py's own values, captured before the person's are applied
_lock = threading.Lock()


def validate(key, value):
    """(ok, clean value or error text)."""
    spec = BY_KEY.get(key)
    if not spec:
        return False, f"{key} is not a setting that can be changed here"
    kind = spec["kind"]
    try:
        if kind == "bool":
            if isinstance(value, str):
                v = value.strip().lower()
                if v not in ("true", "false", "on", "off", "yes", "no", "1", "0"):
                    return False, f"{spec['label']}: on or off"
                value = v in ("true", "on", "yes", "1")
            return True, bool(value)
        if kind in ("int", "float"):
            v = float(value)
            if spec["min"] is not None:
                v = max(spec["min"], v)
            if spec["max"] is not None:
                v = min(spec["max"], v)
            return True, (int(round(v)) if kind == "int" else round(v, 3))
        if kind == "choice":
            v = str(value).strip()
            for c in spec["choices"]:
                if c.lower() == v.lower():
                    return True, c
            return False, f"{spec['label']}: one of {', '.join(spec['choices'])}"
        if kind == "hotkey":
            v = str(value).strip().lower()
            if v == "":
                return True, ""
            parts = v.split("+")
            mods = {"<cmd>", "<ctrl>", "<shift>", "<alt>"}
            if len(parts) < 2 or not all(p in mods for p in parts[:-1]) or not parts[-1] or parts[-1] in mods:
                return False, f"{spec['label']}: like <cmd>+<shift>+e (modifiers first, then one key)"
            return True, v
    except (TypeError, ValueError):
        return False, f"{spec['label']}: not a valid value"
    return False, f"{spec['label']}: not a valid value"


def load(path=None):
    """The person's saved choices (only valid, known ones)."""
    try:
        with open(path or PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for k, v in (raw.items() if isinstance(raw, dict) else ()):
        ok, clean = validate(k, v)
        if ok:
            out[k] = clean
    return out


def apply(namespace, path=None):
    """Called at the end of config.py with its globals()."""
    for s in SETTINGS:
        if s["key"] in namespace and s["key"] not in DEFAULTS:
            DEFAULTS[s["key"]] = namespace[s["key"]]
    for k, v in load(path).items():
        namespace[k] = v
    return namespace


def current(cfg=None):
    if cfg is None:
        import config as cfg
    return {s["key"]: getattr(cfg, s["key"], DEFAULTS.get(s["key"])) for s in SETTINGS}


def save(changes, cfg=None, path=None):
    """Validate, write to settings.json and apply to the running config.
    Returns {"ok", "changed": [...], "restart": [...labels], "errors": [...]}."""
    if cfg is None:
        import config as cfg
    path = path or PATH
    clean, errors = {}, []
    for k, v in (changes or {}).items():
        ok, val = validate(k, v)
        (clean.__setitem__(k, val) if ok else errors.append(val))
    with _lock:
        stored = load(path)
        changed = [k for k, v in clean.items() if getattr(cfg, k, None) != v]
        for k, v in clean.items():
            if DEFAULTS.get(k, object()) == v:
                stored.pop(k, None)               # back to the default: keep the file to real choices
            else:
                stored[k] = v
            setattr(cfg, k, v)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stored, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    restart = [BY_KEY[k]["label"] for k in changed if not BY_KEY[k]["live"]]
    for fn in list(LISTENERS):
        try:
            fn(changed)
        except Exception:
            pass
    return {"ok": not errors, "changed": changed, "restart": restart, "errors": errors}


def reset(cfg=None, path=None):
    return save({k: v for k, v in DEFAULTS.items()}, cfg=cfg, path=path)


LISTENERS = []                # fn(changed_keys): the avatar re-reads gesture speed, etc.
