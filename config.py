"""
Eva — configuration.
Fill NVIDIA_API_KEY before running (env var, or paste directly below).
Get one free at https://build.nvidia.com/settings/api-keys — NVIDIA
Developer Program account only, no credit card. Free tier is ~40 RPM with
a starting credit allotment (not literally unlimited, but no card).
"""
import secret_store

# Environment variable, else the encrypted store (set_key.bat or the tray's
# "Set API key"), else the placeholder. Pasting the key below still works but
# is lost every time the folder is replaced by an update.
NVIDIA_API_KEY = secret_store.get("NVIDIA_API_KEY", "PASTE_YOUR_KEY_HERE")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
# Reasoning / orchestration model. Nemotron 3 supports granular reasoning
# budget control (see REASONING_EFFORT below), which Kimi K3 did not -- it
# reasoned at length on every turn regardless, which is the root of the
# latency and "overthinking" seen in every session so far.
# If latency is still poor, "nvidia/nemotron-3-super-120b-a12b" is the
# lighter sibling aimed at production interactive use; swap it in here.
# 2026-10-28: NVIDIA stopped serving nemotron-3-super-120b-a12b to this key (404 "Function ... Not found
# for account"; tools/models_now.py). The 3.5 Super VL model of the same size answers with tool calls and
# also sees, so one model now does both jobs (Eva 2.0 design: one multimodal model).
MODEL_ID = "nvidia/nemotron-3.5-super-vl-120b-a12b"

# Model providers (providers.py). Any model setting here may be "provider:model-id" -- presets:
# nvidia (default for a bare id), openrouter, openai, groq, together, ollama and lmstudio (local,
# no key). Keys: set_key.bat <KEY_NAME>, e.g. set_key.bat OPENROUTER_API_KEY.
# Tried in order when MODEL_ID keeps answering busy, is retired, refused, or has no key. Empty =
# NVIDIA only, as before. Example:
#   MODEL_FALLBACKS = ("openrouter:nvidia/nemotron-3-super-120b-a12b:free", "ollama:qwen3:8b")
# Benchmark 2026-10-28 (16 tasks, fallbacks off): 3.5 Super VL 16/16 in 158 s; 3 Ultra 13/16 in 408 s (the 3
# failures were a console-echo crash since fixed); MiniMax M3.1 Flash 10/16, all 6 failures 429 rate limits.
# glm-5.3, glm-5.3-flash, gpt-oss-20b timed out. None reported a false "done".
MODEL_FALLBACKS = ("nvidia/nemotron-3-ultra-550b-a55b", "minimaxai/minimax-m3.1-flash")
# More OpenAI-compatible endpoints, or a changed preset:
#   PROVIDERS = {"azure": {"base_url": "https://<name>.openai.azure.com/openai/v1", "key": "AZURE_OPENAI_KEY"}}
PROVIDERS = {}

# Vision model, used ONLY as a perception fallback (see vision.py). Kept
# separate and deliberately small: perception can run on many steps, and
# a general vision LLM costs seconds per call, versus ~50ms for the
# accessibility tree. NVIDIA's own guidance names this model for
# "screenshots ... and UI understanding".
# nemotron-nano-12b-v2-vl reached END OF LIFE on 2026-08-26 and now returns
# HTTP 410 Gone on every call -- so the entire vision fallback has been dead
# since then, which is why Eva could not check her own work on screen.
# Replacement: NVIDIA's current perception model, image+video+text, free
# endpoint on build.nvidia.com. Verify it resolves on your key; if it 410s
# too, pick a current one from https://build.nvidia.com/models and change
# this line.
# 2026-10-17: Nemotron 3.5 Super VL (released 2026-10-15, free endpoint, "early access"):
# the large model with vision built in, replacing the small Nano Omni as the primary
# eye. Its coordinate format is measured on first use (vision.calibrate). The previous
# model stays first in line behind it.
VISION_MODEL_ID = "nvidia/nemotron-3.5-super-vl-120b-a12b"
# Her own windows (body, bubble, chat, dialogs) are painted grey in every picture sent to the
# vision model: nothing of them leaves the PC, and she never describes herself as "the screen".
VISION_MASK_OWN_WINDOWS = True

# Tried in order when the vision model above is busy (503) or retired (404/410).
# Each one's coordinate convention is measured once (vision.py, calibrate).
# Names from NVIDIA's catalogue; one your key cannot use is skipped by itself.
VISION_FALLBACK_MODELS = ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",   # the eye until 2026-10-17
                          "google/gemma-4-31b-it")       # qwen3.5-122b answered 410 Gone (eva.log 2026-10-11 22:59)
# How the vision model's boxes are read: "auto" (measured per model, default),
# or force "norm1000", "norm1000_yx", "norm1", "pixels".
VISION_COORDS = "auto"
VISION_REQUEST_TIMEOUT = 25        # seconds per vision request (no hidden retries)
VISION_LOCATE_BUDGET = 60          # seconds for one locate_on_screen, all passes and models together

# A model call is cut short when its reasoning runs away (eva.log 2026-10-12 00:05: it went round
# in circles for 70 s): longer than this many seconds before any answer, longer than this many
# characters, or repeating itself. She is then told to decide from what she knows.
REASONING_MAX_SECONDS = 45
REASONING_MAX_CHARS = 16000

# "low" | "medium" | "high". Eva's work is mostly "pick the right tool and
# call it", which does not need deep deliberation; raise this only if
# decision quality is visibly suffering.
REASONING_EFFORT = "low"

IDLE_ANIMATION_START_SECONDS = 90
IDLE_NAP_SECONDS = 600
# Her own life when you leave her be (life.py): sitting on windows and the taskbar,
# looking around, stretching, dozing off, waving when you come back.
IDLE_LIFE = True
# The human connection (2026-10-17): eye contact, her eyes following the cursor near her, a nod
# when spoken to, weight shifts and fidgets that grow when nobody has needed her for a while.
PRESENCE = True
# Bored for a while with the person around: she looks around the PC (disk, old downloads, memory
# and CPU hogs, Recycle Bin, open windows, battery) and OFFERS one fix. Never acts on her own.
PATROL = True
# How far she sinks when she sits down (fraction of her height; her hips go to the edge).
SIT_DROP = 0.46
SUGGESTION_CHECK_INTERVAL_SECONDS = 180
SUGGESTION_MIN_GAP_SECONDS = 300
SUGGESTION_SUPPRESS_RECENT_INPUT_SECONDS = 15

# Unprompted remarks. Both OFF by default: Eva speaks when spoken to.
# PROACTIVE_COMMENTARY_ENABLED: a short remark about the window the person has
# settled on ("Looks like you're in Command Prompt...").
# PROACTIVE_SUGGESTIONS: the periodic idle check ("lots of tabs open...").
PROACTIVE_COMMENTARY_ENABLED = False
PROACTIVE_SUGGESTIONS = False
COMMENTARY_DWELL_SECONDS = 20      # how long a window must stay in front first
COMMENTARY_MIN_GAP_SECONDS = 600   # at most one remark per 10 minutes

# Whether to echo the model's own reasoning_content back in the assistant
# message on the next turn. Kimi K3's model card asks for this. Nemotron 3
# on NVIDIA's endpoint MISBEHAVES with it: the echoed reasoning acts as a
# prefill and the model continues it into the content channel, so Eva ends
# up speaking her own planning notes. Keep False unless a model documents
# that it needs this.
SEND_REASONING_BACK = False

# --- Speech recognition ---------------------------------------------------
# Raise if requests are still being cut short; this is a hard ceiling on a
# single capture, not a target length.
STT_PHRASE_TIME_LIMIT = 20          # seconds (was an unconfigurable 8)

# Silence needed before Eva treats a request as finished. Too low and an
# ordinary mid-sentence breath splits one request into two fragments.
STT_PAUSE_THRESHOLD = 1.3           # seconds (was 0.8, then 1.1: a breath mid-request still split it, 2026-10-17 16:04)

# Minimum speech duration for a capture to count as a phrase. The library
# default of 0.3 is longer than a spoken "yes" or "stop", so short
# commands were dropped before recognition.
STT_PHRASE_THRESHOLD = 0.15         # seconds

# Audio kept either side of the phrase, so leading/trailing consonants
# are not clipped. Automatically capped at STT_PAUSE_THRESHOLD.
STT_NON_SPEAKING_DURATION = 0.8     # seconds (library default 0.5; raised, not lowered)

# False pins the loudness gate to the startup calibration. Left True, the
# library drifts it upward during quiet stretches until quietly spoken
# single words no longer register, and it does not recover.
STT_DYNAMIC_ENERGY = False

# Eva's own phrase detection on a microphone that is read without a break (voice.Endpointer). The
# library's listen() threw away the run-up every second nobody spoke, clipping the first sound of a
# request ("she cuts off my mic at the beginning", 2026-10-17 21:13).
# Multi-step jobs on screen (core/operator.py): the main model keeps the plan, the facts learned and
# what each step should show; the vision model only sees and points. Off = the vision model alone.
OPERATOR_PLANNER = True
OPERATOR_PLANNER_EFFORT = "low"     # "medium" is used automatically after a step that did not work

# Eva 2.0 immediate gains (2026-10-28; HANDOFF section 60).
TOOL_GROUPS = True          # send the core tools plus the groups she opens (core/toolbox.py); False = all 136
OPERATOR_VISION_THINKING = True  # the operator's eyes think before answering: 6/6 boards read right vs 0/6 (2026-10-28)
FEELINGS = True             # her energy and mood shape her body; replies may start with "[feeling]"
SMOOTH_MOTION = True        # her walk follows the clock (eased), 60 fps while moving; False = the old fixed steps
ONSCREEN_HANDOFF = 6        # after this many screen steps of her own in one task, hand it to operate_computer; 0 = off
CHECK_BEFORE_DONE = True    # after on-screen steps, one fresh look at the result before she says "done"
# Requests per minute per provider, shared by every model call (text, vision, browser agent). NVIDIA's
# free tier allows about 40 a minute per key across all models (observed, not published); 0 = no pacing.
PROVIDER_RPM = {"nvidia": 30}     # 36 was too close: a fast model met 429s at ~35 a minute (2026-10-28)

STT_OWN_ENDPOINTING = True
STT_PREROLL_SECONDS = 1.0           # audio kept from BEFORE the voice crossed the gate
STT_SHORT_PHRASE_PAUSE = 2.0        # a short opening ("I think", "open") waits this long for the rest
STT_KEEP_PHRASES = 20               # the last N phrases kept as WAV + what was heard (~/.eva/hearing), 0 = none

STT_CALIBRATION_SECONDS = 1.0       # ambient-noise sample at startup
STT_INPUT_DEVICE = "auto"           # "auto": a connected headset/earbuds first, else Windows' default; or part of a name
STT_MIN_GATE = 12                   # the loudness gate never goes below this (2026-10-28: room 4 on the laptop mic)
STT_GATE_FACTOR = 3.0               # gate = the room's level x this (the switch-on click is skipped)
STT_MAX_ENERGY_THRESHOLD = 1200     # ceiling, so a noisy calibration cannot deafen Eva

# Edge neural voice. `edge-tts --list-voices` for the catalogue; names
# follow <locale>-<Name>Neural, e.g. "en-GB-SoniaNeural", "en-US-GuyNeural".
TTS_VOICE = "en-US-AriaNeural"
# 2026-10-28 (tools/tts_trial.txt): NVIDIA Magpie started speaking in ~0.5 s against 1.43 s for the Edge voice.
# "nvidia" = Magpie first, then Edge, then the Windows voice; "edge" = as before.
TTS_BACKEND = "nvidia"
TTS_NVIDIA_VOICE = "Magpie-Multilingual.EN-US.Aria"     # also .Mia, .Jason; moods like .Mia.Calm
TTS_NVIDIA_RETRY_SECONDS = 120
# The Windows voice used when the online one is unreachable (any installed SAPI voice whose
# name contains this; "Zira" is the female English voice every Windows 10/11 has).
TTS_LOCAL_VOICE = "Zira"
TTS_LOCAL_RATE = 1                  # -10 .. 10

# Close the microphone device while Eva speaks. False (the default) keeps it
# open and relies on the mic-ignore window instead, which removes the deaf
# gap that clipped the start of the person's next sentence. Set True only if
# Eva starts transcribing her own voice.
PAUSE_MIC_WHILE_SPEAKING = False

# How long to keep ignoring heard audio after playback finishes, for the
# recognizer's own tail. Playback now blocks until it genuinely ends, so
# this no longer has to cover a guess at the utterance length.
MIC_IGNORE_TAIL_SECONDS = 0.35

# --- agent_message safety ------------------------------------------------
# agent_message can POST arbitrary JSON anywhere the machine can route to.
# Restricted to public https endpoints; private, loopback and link-local
# addresses are refused outright. Add "http" here only if you genuinely
# need it.
AGENT_MESSAGE_ALLOWED_SCHEMES = ("https",)
AGENT_MESSAGE_MAX_RESPONSE_CHARS = 4000

# --- Browser -------------------------------------------------------------
# Eva's browser profile lives in ~/.eva/browser-profile, so cookies,
# logins and cookie-banner consent survive between sessions.
BROWSER_VIEWPORT = (1440, 900)
BROWSER_LOCALE = "en-IN"
BROWSER_TIMEZONE = "Asia/Kolkata"
# Empty string = Playwright's own Chrome user agent. Set this only if you
# need a specific one; it must match the Chromium version actually
# installed or sites will serve the wrong layout.
BROWSER_USER_AGENT = ""

# Pause between browser steps, seconds (min, max). Playwright fires
# actions instantly and at perfectly even intervals; a small random pause
# makes the page behave the way it would for a person, and gives
# JS-heavy sites time to settle between steps -- which was also causing
# clicks to land before the page had finished reacting to the last one.
BROWSER_STEP_DELAY = (0.4, 1.3)

# --- Autonomy and permissions ---------------------------------------------
# Eva acts on her own and asks about exactly two things:
#   DESTROY    an action that can lose or overwrite something (overwriting a
#              file, moving files, stopping a process, select-all-delete, a
#              command that is not known to be read-only)
#   FINANCIAL  a step that spends money (a "Place order" button, a payment
#              field, a card number)
# Everything else -- typing, clicking, browsing, sending a message, writing a
# new file -- runs without asking. Other effects can be added here if you
# want them gated: OBSERVE, INTERACT, MODIFY, SENSITIVE, EXTERNAL (external =
# leaves the machine: a message, an upload).
CONFIRM_EFFECTS = ("DESTROY", "FINANCIAL")

# How much Eva performs around an action: "full" (walk, look, highlight,
# react), "brisk" (react only to problems), or "off". She also drops to
# brisk by herself during rapid batches or when the person is waiting.
EMBODIMENT_MODE = "full"
# Slow work only earns a status line after this many seconds, so quick calls
# do not flicker one.
EMBODIMENT_STATUS_DELAY = 0.5

# Hand-animated poses (nod, shrug, think, type, read, point, beckon, cheer)
# and gaze, layered on the VRM skeleton. Written without being able to see
# the rig: if a pose looks wrong (arms the wrong way, head turning away
# from its target), flip the matching sign constant at the top of
# web/avatar_3d.html, or set this False to fall back to the classic clips.
PROCEDURAL_GESTURES = True

# --- Back-end tools ----------------------------------------------------------
WRITE_FILE_MAX_BYTES = 1_000_000
RUN_COMMAND_TIMEOUT = 30          # seconds; hard ceiling 120
RUN_COMMAND_MAX_OUTPUT = 6000     # characters per stream

# --- Memory and audit -----------------------------------------------------
# Eva remembers facts, past tasks and what has worked in each app, in one
# small file on this machine. Delete the file and she forgets everything;
# ask her to "forget everything" and she will (she asks first). Empty path =
# ~/.eva/memory.db and ~/.eva/audit.jsonl.
MEMORY_ENABLED = True
MEMORY_DB_PATH = ""
AUDIT_LOG_PATH = ""


# --- Audio playback -------------------------------------------------------
# Seconds the output stays open after the player reports the clip finished.
# Closing at the instant of "stopped" clips the last syllable on some devices.
TTS_TAIL_SECONDS = 0.45
# Wake the audio output with a silent clip while the sentence is synthesised,
# so the first syllable is not swallowed by an output that went to sleep.
TTS_PRIME = True

# --- WebEngine (avatar renderer) -------------------------------------------
# Extra Chromium switches for the avatar's WebGL view, applied before Qt
# starts. The default disables Chromium's native-window occlusion tracking:
# with a transparent click-through always-on-top stage plus several other
# always-on-top overlay windows, Chromium can decide the stage is "covered"
# and drop/re-show frames each time a bubble or overlay appears.
WEBENGINE_FLAGS = ("--disable-features=CalculateNativeWinOcclusion "
                   "--disable-backgrounding-occluded-windows "
                   "--disable-renderer-backgrounding")

# Renderer experiment for the avatar flicker. Change ONE value, restart, watch.
#   "default"         occlusion-tracking switches only (WEBENGINE_FLAGS above)
#   "no_gpu_comp"     also disables Chromium's GPU compositing
#   "software"        software rendering for the avatar (lower fps, but rules
#                     the GPU/driver in or out completely)
#   "angle_d3d11"     forces the D3D11 ANGLE backend (common fix for flicker on
#                     transparent always-on-top windows with some drivers)
RENDER_MODE = "default"

# How the 3-D stage itself is drawn and composed (see tools/flicker_bench.py,
# run by flicker_test.bat, which measures each choice on YOUR screen):
#   QT_GRAPHICS_API         "" = Qt's default (Direct3D 11 on Windows), or
#                           "opengl" / "d3d12" / "software"
#   AVATAR_PRESERVE_BUFFER  keep the last drawn image between frames
#   AVATAR_FPS              how often the scene is drawn (30 or 60)
QT_GRAPHICS_API = ""
# How Qt composes Eva's window: "gpu" (default) or "software". "software" only
# takes effect together with RENDER_MODE = "software": on Windows it turns
# Chromium's GPU off entirely, so with any GPU render mode WebGL cannot start
# and she is invisible (eva.log 2026-10-11 16:59 -- "Error creating WebGL context").
QT_COMPOSITION = "gpu"
AVATAR_PRESERVE_BUFFER = True
AVATAR_FPS = 30

# The flicker bench writes the measured winner to ~/.eva/render.json; when that
# file exists its values replace the four above. Delete it to go back.
def _render_override():
    import json, os
    path = os.path.join(os.path.expanduser("~"), ".eva", "render.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    allowed = {"RENDER_MODE": str, "QT_GRAPHICS_API": str, "QT_COMPOSITION": str, "AVATAR_PRESERVE_BUFFER": bool,
               "AVATAR_FPS": int}
    return {k: v for k, v in data.items() if k in allowed and isinstance(v, allowed[k])}


RENDER_OVERRIDE = _render_override()
globals().update(RENDER_OVERRIDE)


# One start with software drawing after WebGL failed to start (main.py writes
# ~/.eva/render-fallback.json and restarts; the file is used once, then removed,
# so the next normal start tries the GPU again).
def _render_fallback():
    import json, os
    path = os.path.join(os.path.expanduser("~"), ".eva", "render-fallback.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        os.remove(path)
    except (OSError, ValueError):
        return {}
    return {"RENDER_MODE": "software", "QT_GRAPHICS_API": "", "reason": str(data.get("reason", ""))[:200]}


RENDER_FALLBACK = _render_fallback()
if RENDER_FALLBACK:
    RENDER_MODE, QT_GRAPHICS_API = RENDER_FALLBACK["RENDER_MODE"], RENDER_FALLBACK["QT_GRAPHICS_API"]
RENDER_MODE_FLAGS = {
    "default": "",
    "no_gpu_comp": "--disable-gpu-compositing",
    "software": "--disable-gpu --disable-gpu-compositing --use-gl=angle --use-angle=swiftshader",
    "angle_d3d11": "--use-angle=d3d11 --disable-gpu-vsync",
}

# --- Global shortcuts (pynput syntax; "" turns one off) -------------------------
# Open Eva's chat from any app (the text you have selected comes along):
HOTKEY_CHAT = "<cmd>+<shift>+e"
# Drag a box around part of the screen and ask about it:
HOTKEY_REGION = "<cmd>+<shift>+w"
# Stop everything she is doing -- a repeat, a game, a task -- from any app:
HOTKEY_STOP = "<cmd>+<shift>+x"

# --- Helpers (background workers, core/workers.py) -----------------------------
# How many may work at once. Each makes its own calls to the model.
HELPER_MAX = 3

# --- Avatar size ---------------------------------------------------------------
# 1.0 = the original 200 x 380 px; 0.5 = half that size (the person's choice,
# 2026-10-11), then 15% larger: 0.575 (115 x 219 px). Dragging her corner still resizes her.
AVATAR_SCALE = 0.575

# --- Avatar life and pose tuning ---------------------------------------------
# Blinking, mouth movement while she speaks, and small head motion. Turn off if
# it ever looks wrong; nothing else depends on it.
AVATAR_LIFE = True
# Values from the pose lab (run pose_lab.bat, adjust until she looks right, press
# "Show settings", paste here). Empty = the built-in defaults.
POSE_TUNING = {}

# --- Speech recognition backend ------------------------------------------------
# "google": the free web recognizer (needs internet; audio leaves the machine).
# "phonon":  local Phonon-2 through `fermion serve` (see stt_local.py,
#            start_phonon.bat). Falls back to Google automatically if the local
#            server is not running, so switching it on cannot leave her deaf.
# "nvidia": NVIDIA's free speech recognisers with the model key (stt_nvidia.py). Falls back to Google for a
# minute whenever the service fails. 2026-10-28, the 14 kept phrases (tools/asr_trial.txt): Google dropped
# sentence openings the audio held ("go to the top of the browser and ...", "Anyways, ...") and misheard
# words ("Centre button", "become that sell", "place value"); canary-1b kept every opening, heard those
# right, stayed silent on silence (whisper invents "you"), 0.25 s a phrase on the live path vs 0.88 s.
STT_BACKEND = "nvidia"
STT_NVIDIA_MODEL = "canary-1b"
STT_NVIDIA_RETRY_SECONDS = 60
STT_LOCAL_URL = "http://127.0.0.1:8000"
STT_LOCAL_API_KEY = ""
STT_LOCAL_TIMEOUT = 15
STT_LOCAL_RETRY_SECONDS = 30

# --- Skills, learning, connections, web reading (2026-10-10) -----------------
# Skills: SKILL.md folders (the open Agent Skills format) in <app>/skills and
# ~/.eva/skills. Add more folders here, e.g. a clone of anthropics/skills.
SKILL_DIRS = ()
# After a task of at least LEARN_MIN_TOOL_CALLS steps, or a correction from
# the person, a background review may save or improve a skill. Never for a
# task that read web pages or connected services.
LEARN_SKILLS = True
LEARN_MIN_TOOL_CALLS = 4
# Connected services (MCP) are listed in ~/.eva/connections.json. On first run
# Exa's free public search server is added, which gives Eva web search.
CONNECT_EXA_SEARCH = True
# read_web_page: "jina" asks Jina Reader (r.jina.ai) to render the page, which
# handles JavaScript sites but lets Jina see the address; "direct" fetches it
# from this PC only.
WEB_READER = "jina"
# Speaking over Eva (two words or more that are not her own) stops her speech
# and is taken as your next request. False = only "stop" interrupts her.
BARGE_IN = True

# --- Replies (2026-10-10, 2026-10-11) -----------------------------------------
# A reply longer than this many characters is not spoken in full: she says one
# short line, and the whole text is shown in her message bubble (it stays up
# to a minute, and while the pointer is over it) and kept in the chat window
# (~/.eva/chat-history.jsonl). The bubble has a "Notepad" link to save it as a
# file. 0 turns the limit off.
LONG_REPLY_CHARS = 100
# Where a long reply goes: "bubble" (the bubble and the chat window) or
# "notepad" (Documents\Eva\<date time> - <first words>.txt, opened in Notepad).
LONG_REPLY_TARGET = "bubble"
# Message bubble colours: "blue" (Eva in blue, you in green), "green" (Eva in
# green, you in blue) or "grey" (iMessage: Eva in light grey, you in blue).
BUBBLE_COLOURS = "blue"
# ...except a plain spoken answer: at most two sentences, no list, up to this many characters.
SPOKEN_MAX_CHARS = 240

# Gesture and pose speed (Settings > Look and movement). 1.0 = as measured.
ANIMATION_SPEED = 1.0

# --- Predictive help (predictive.py) --------------------------------------------
# She notices, on this PC only, what you usually open at a given time of day
# (an app, a website, a folder) and OFFERS it in her bubble ("You usually open
# Outlook around now -- shall I?"). Nothing runs without your yes. Anything to
# do with money (banking, payments, shopping checkouts, trading) is never
# recorded or offered. Off by default; tray > Settings > Initiative.
PREDICTIVE_HELP = False
PREDICTIVE_MIN_SEEN = 3            # seen on at least this many different days
PREDICTIVE_WINDOW_MINUTES = 45     # "around now": within this many minutes of the usual time


# --- The person's own choices (tray > Settings), kept in ~/.eva/settings.json --
def _apply_user_settings():
    try:
        import settings_store
        settings_store.apply(globals())
    except Exception:
        pass                           # a broken settings file must never stop Eva starting

# --- Replays of tasks that went wrong (core/replay.py) --------------------------
# A task whose last action failed keeps a step-by-step page with a screenshot
# after each on-screen action: ~/.eva/replays (tray > Failed tasks). Local only;
# no picture after a step that handles passwords or payments.
REPLAY_FAILED_TASKS = True
REPLAY_SCREENSHOTS = True
REPLAY_KEEP = 10

# Must stay the LAST line: the person's choices override every default above.
_apply_user_settings()
