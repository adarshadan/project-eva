"""Eva -- the first-run welcome: what it shows, what it remembers. Pure logic (welcome_ui.py draws it).

Plan to v1.0, Phase 2 ("First-run onboarding"): today the first run is an API key prompt in the tray,
then silence -- Eva assumes the person already knows what she can do and how to talk to her. The
welcome takes about a minute and is shown once (again from tray > Welcome tour):

    1. Hello          what she does, and what she never does without asking (anything that cannot be
                      undone, anything to do with money)
    2. Her model key  only when none is set: paste it, she checks it with one tiny request
    3. Voice and size pick a voice (she says a line in it) and a size
    4. Microphone     say "Hello Eva": she shows what she heard; or keep the mic off
    5. Privacy        what leaves this PC, to whom, and how to keep it local -- read off the
                      running configuration, not written by hand
    6. Try it         three first tasks she performs live; each one opens the chat window, where
                      the task card shows what she does

State: ~/.eva/welcome.json {"version", "finished" | "skipped", "at"}.
"""
import json
import os
import time

PATH = os.path.join(os.path.expanduser("~"), ".eva", "welcome.json")
VERSION = 1
KEY_URL = "https://build.nvidia.com/settings/api-keys"

STEPS = ("hello", "key", "voice", "mic", "privacy", "try")

SIZES = (("Small", 0.45), ("Medium", 0.575), ("Large", 0.75))

VOICE_NAMES = {"en-US-AriaNeural": "Aria (US)", "en-US-JennyNeural": "Jenny (US)", "en-US-AnaNeural": "Ana (US, young)",
               "en-IN-NeerjaNeural": "Neerja (India)", "en-GB-SoniaNeural": "Sonia (UK)",
               "en-AU-NatashaNeural": "Natasha (Australia)", "en-US-GuyNeural": "Guy (US, male)"}

# The first tasks: safe, quick, and each shows a different side of her. None moves or deletes
# anything; the Downloads one proposes and waits.
FIRST_TASKS = (
    ("What's on my screen?", "What is on my screen right now? Describe it in two or three sentences.",
     "I read the window in front: its structure and text."),
    ("Find a file", "Find the five files I changed most recently in my Documents folder and list them.",
     "My own instant index of your folders."),
    ("Tidy my Downloads (a plan only)",
     "Look through my Downloads folder and propose how to tidy it: group the files by type with their sizes and "
     "point out duplicates and old installers. Move or delete nothing; just show me the plan.",
     "I propose; nothing is moved until you agree."),
)


# ---- whether to show it --------------------------------------------------------------------------
def state(path=None):
    try:
        with open(path or PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def needed(path=None) -> bool:
    """Shown until it was finished or skipped once for this version."""
    s = state(path)
    return not (s.get("finished") or s.get("skipped")) or int(s.get("version", 0)) < VERSION


def mark(how: str, path=None, **extra):
    """how: 'finished' | 'skipped'."""
    path = path or PATH
    d = {"version": VERSION, how: True, "at": time.strftime("%Y-%m-%d %H:%M:%S"), **extra}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass
    return d


def steps(key_missing: bool):
    """The pages to show: the key page only when she has no working key."""
    return [s for s in STEPS if s != "key" or key_missing]


# ---- her model key ---------------------------------------------------------------------------------
def looks_like_key(text: str):
    """(ok, why) before any request: catches a pasted sentence, a URL or the placeholder."""
    t = str(text or "").strip()
    if not t:
        return False, "Paste the key first."
    if "PASTE_YOUR_KEY_HERE" in t or " " in t or t.startswith("http"):
        return False, "That doesn't look like a key. Copy only the key itself."
    if len(t) < 20:
        return False, "That key looks too short. Check that all of it was copied."
    return True, ""


def check_key(key: str, ask=None):
    """(ok, message): one tiny request with this key. ask(key) -> raises on failure; injectable."""
    ok, why = looks_like_key(key)
    if not ok:
        return False, why
    if ask is None:
        ask = _ask_model
    try:
        ask(key.strip())
    except Exception as e:
        text = str(e)
        code = getattr(e, "status_code", None)
        if code == 401 or "401" in text or "nauthorized" in text:
            return False, "The key was refused (401). Check that it was copied whole and is still active."
        if code == 403 or "403" in text:
            return False, "The key works but is not allowed to use my model (403). Check the key's access."
        if code == 429 or "429" in text:
            return True, "The key works (the service is busy right now, which is normal)."
        return False, f"Could not reach the model service: {text[:120]}"
    return True, "The key works."


def _ask_model(key):
    import config
    import providers
    from openai import OpenAI
    name, model = providers.parse(config.MODEL_ID)
    c = OpenAI(api_key=key, base_url=providers.spec(name)["base_url"], timeout=30, max_retries=0)
    c.chat.completions.create(model=model, messages=[{"role": "user", "content": "Say OK."}], max_tokens=2)


def save_key(key: str):
    """Into the encrypted store, and live: no restart needed."""
    import config
    import providers
    import secret_store
    secret_store.set("NVIDIA_API_KEY", key.strip())
    config.NVIDIA_API_KEY = key.strip()
    providers.reset_clients()


# ---- what leaves this PC ---------------------------------------------------------------------------
def _host(url):
    u = str(url or "")
    return u.split("://", 1)[-1].split("/", 1)[0] or u


def privacy(cfg=None):
    """[(what, where, how to keep it on this PC)] for the RUNNING configuration."""
    if cfg is None:
        import config as cfg
    rows = []
    try:
        import providers
        name, _ = providers.parse(getattr(cfg, "MODEL_ID", ""))
        p = providers.spec(name)
        local = bool(p.get("local"))
        host = _host(p.get("base_url"))
    except Exception:
        local, host = False, "the model service"
    if local:
        rows.append(("What you ask me, and what I read on screen to answer", f"Stays on this PC ({host})", ""))
    else:
        rows.append(("What you ask me, and the text I read on screen to answer it", host,
                     "Use a local model (Ollama or LM Studio) in config.py MODEL_ID"))
    vision = getattr(cfg, "VISION_MODEL_ID", "")
    if vision:
        try:
            import providers
            vname, _ = providers.parse(vision)
            vlocal = bool(providers.spec(vname).get("local"))
            vhost = _host(providers.spec(vname).get("base_url"))
        except Exception:
            vlocal, vhost = False, "the model service"
        if not vlocal:
            rows.append(("A screenshot, only when structure and text are not enough", vhost,
                         "My own windows are masked out; set a local VISION_MODEL_ID to keep it here"))
    stt = str(getattr(cfg, "STT_BACKEND", "google")).lower()
    if stt == "nvidia":
        rows.append(("Your voice, while the microphone is on", "NVIDIA speech recognition (grpc.nvcf.nvidia.com)",
                     "Mute the mic, or choose 'phonon' (on this PC) in Settings"))
    elif stt == "google":
        rows.append(("Your voice, while the microphone is on", "Google speech recognition",
                     "Mute the mic, or choose 'phonon' (on this PC) in Settings"))
    else:
        rows.append(("Your voice", "Stays on this PC (phonon)", ""))
    if str(getattr(cfg, "TTS_BACKEND", "edge")).lower() == "nvidia":
        rows.append(("What I say aloud (the text, to make the voice)", "NVIDIA speech synthesis "
                     "(grpc.nvcf.nvidia.com), else Microsoft Edge", "If both are unreachable I use the Windows voice"))
    else:
        rows.append(("What I say aloud (the text, to make the voice)", "Microsoft Edge voice service",
                     "If it is unreachable I use the Windows voice on this PC"))
    if int(getattr(cfg, "STT_KEEP_PHRASES", 0) or 0) > 0:
        rows.append((f"The last {int(cfg.STT_KEEP_PHRASES)} phrases I heard, as sound", "Stay on this PC "
                     "(.eva\\hearing), to check my hearing", "Settings > Privacy: keep 0"))
    rows.append(("Memory, chat history, replays, settings, your key", "Stay on this PC (your user folder; "
                 "the key encrypted for your Windows account)", ""))
    return rows
