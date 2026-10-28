"""Eva Doctor -- finds out what actually works on THIS computer.

Run it (double-click doctor.bat, or `python eva_doctor.py`), answer two
yes/no questions about the audio, and send back eva-doctor-report.txt.

Why it exists: Eva's code is written without being able to run Windows, hear
audio or see the screen. Guessing from logs has cost many rounds. This runs
the real operations against real windows and reports, per item, PASS / FAIL /
INFO with timings, and where anything hangs.

What it touches: it opens Notepad, types a line, clears it and closes it; it
opens example.com in Eva's browser (and in Brave/Chrome if found) and closes
it; it makes one short speech clip. It does not touch your other windows,
files, settings or accounts. Do not click or type while it runs (about a
minute); it moves the cursor.

Options:  --no-prompt   skip the two audio questions
          --no-browser  skip the browser checks
          --no-audio    skip speech
          --no-model    skip the language-model check
"""
import os
import platform
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)
os.environ.setdefault("EVA_LOG_PATH", os.path.join(tempfile.gettempdir(), "eva-doctor.log"))

REPORT_PATH = os.path.join(HERE, "eva-doctor-report.txt")
ARGS = set(sys.argv[1:])

RESULTS = []          # (status, name, seconds, detail)
STATE = {}            # values handed from one check to the next


def record(status, name, seconds, detail=""):
    RESULTS.append((status, name, seconds, detail))
    mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "INFO": " info ", "SKIP": " skip "}[status]
    print(f"[{mark}] {name}  ({seconds:.1f}s)" + (f"  {detail.splitlines()[0]}" if detail else ""))
    sys.stdout.flush()


def run(name, fn, timeout=30.0):
    """Runs one check under a time limit. fn returns a detail string (pass) or
    raises AssertionError / Exception (fail). Returns True on pass."""
    import deadline
    t0 = time.monotonic()
    try:
        out = deadline.run_with_deadline(name, fn, timeout, slow_after=timeout + 1)
    except AssertionError as e:
        record("FAIL", name, time.monotonic() - t0, str(e) or "assertion failed")
        return False
    except Exception as e:
        tb = "".join(traceback.format_exception_only(type(e), e)).strip()
        record("FAIL", name, time.monotonic() - t0, f"{tb}\n{traceback.format_exc()[-900:]}")
        return False
    dt = time.monotonic() - t0
    if isinstance(out, dict) and out.get("timed_out"):
        record("FAIL", name, dt, f"HUNG: did not finish within {timeout:.0f}s -- this is the kind of call "
                                 "that freezes Eva")
        return False
    record("PASS", name, dt, out if isinstance(out, str) else "")
    return True


def info(name, fn, timeout=15.0):
    import deadline
    t0 = time.monotonic()
    try:
        out = deadline.run_with_deadline(name, fn, timeout, slow_after=timeout + 1)
        if isinstance(out, dict) and out.get("timed_out"):
            out = "(timed out)"
    except Exception as e:
        out = f"(unavailable: {e})"
    record("INFO", name, time.monotonic() - t0, str(out))


def skip(name, why):
    record("SKIP", name, 0.0, why)


# --------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------
def check_imports():
    import importlib
    missing = []
    for mod in ("openai", "PySide6", "pyautogui", "pygetwindow", "pywinauto", "mss", "psutil",
                "speech_recognition", "pyaudio", "pynput", "screeninfo", "playwright", "requests",
                "edge_tts", "playsound", "pyperclip", "PIL"):
        try:
            importlib.import_module(mod)
        except Exception as e:
            missing.append(f"{mod} ({type(e).__name__})")
    assert not missing, "missing or broken: " + ", ".join(missing) + "  -> run: pip install -r requirements.txt"
    return "all required packages import"


def check_eva_modules():
    import importlib
    bad = []
    for mod in ("config", "desktop_control", "app_resolver", "browser_agent", "voice", "vision", "core.agent",
                "core.runtime", "core.speaker", "core.llm", "core.toolset", "core.activity",
                "permissions", "memory", "audit", "deadline", "ui_navigate", "backend_tools", "stt_local", "secret_store"):
        try:
            importlib.import_module(mod)
        except Exception as e:
            bad.append(f"{mod}: {type(e).__name__}: {e}")
    assert not bad, "; ".join(bad)
    return "all Eva modules import"


# Tools whose implementation needs the live avatar or memory and is bound when
# Eva starts; everything else must already have one.
BOUND_AT_STARTUP = {"delegate", "helpers_status", "cancel_helper", "play_animation", "stop_animation", "walk_to_screen", "go_home", "run_recording",
                    "remember", "recall", "forget", "forget_all", "save_skill"}


def check_tool_registry():
    import core.toolset as declarations
    from core import registry
    assert declarations
    problems = [p for p in registry.validate()
                if not any(f"'{n}' has no implementation" in p for n in BOUND_AT_STARTUP)]
    assert not problems, "; ".join(problems)
    return f"{len(registry.names())} tools declared, each once"


def check_environment():
    import ctypes
    lines = [f"{platform.platform()}  python {platform.python_version()}"]
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        lines.append(f"screen {user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)}, "
                     f"monitors {user32.GetSystemMetrics(80)}")
    except Exception:
        pass
    return "; ".join(lines)


def check_gpu():
    import subprocess
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + ' | driver ' + $_.DriverVersion }"],
        capture_output=True, text=True, timeout=12,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    return out.replace("\r", "").replace("\n", " ;; ") or "(none reported)"


def check_key_source():
    import secret_store
    return f"NVIDIA_API_KEY comes from: {secret_store.status('NVIDIA_API_KEY')}"


def check_skills_and_services():
    from core.connections import CONNECTIONS
    from core.skills import LIBRARY
    LIBRARY.refresh(force=True)
    names = sorted(LIBRARY.all())
    CONNECTIONS.connect_all()
    return f"skills: {len(names)} ({', '.join(names[:12])}{'...' if len(names) > 12 else ''}); services: {CONNECTIONS.summary()}"


def check_web_reading():
    from core import web_reach
    r = web_reach.read_web_page("https://example.com")
    assert r.get("ok"), r.get("error")
    return f"read example.com via {r['via']} ({len(r['text'])} chars)"


def check_model():
    import config
    key = config.NVIDIA_API_KEY
    assert key and key != "PASTE_YOUR_KEY_HERE", "NVIDIA_API_KEY is not set (set the environment variable, then restart)"
    from openai import OpenAI
    client = OpenAI(base_url=config.NVIDIA_BASE_URL, api_key=key, timeout=45, max_retries=1)
    tools = [{"type": "function", "function": {
        "name": "get_number", "description": "Returns a number the user asked for.",
        "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}}}]
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=config.MODEL_ID, temperature=0.1, max_tokens=400, tools=tools,
        messages=[{"role": "user", "content": "Call get_number with n=7. Do not answer in words."}])
    dt = time.monotonic() - t0
    calls = resp.choices[0].message.tool_calls or []
    assert calls, f"the model answered in words instead of calling the tool ({dt:.1f}s) -- weak tool use"
    assert calls[0].function.name == "get_number", f"wrong tool: {calls[0].function.name}"
    return f"tool call works, round trip {dt:.1f}s ({config.MODEL_ID})"


# --------------------------------------------------------------------------
# desktop: one real window, driven end to end
# --------------------------------------------------------------------------
def d_launch():
    import desktop_control as dc
    r = dc.launch_app("notepad")
    assert r.get("ok"), f"launch failed: {r.get('error')}"
    win = r.get("window")
    if not win:
        time.sleep(1.5)
        wins = [w for w in dc.list_windows().get("windows", []) if "Notepad" in w["title"]]
        assert wins, "Notepad started but no window appeared"
        win = wins[0]
    STATE["np"] = win["handle"]
    return f"handle {win['handle']} ({win['title']}) found via {r.get('found_via')}"


def d_listed():
    import desktop_control as dc
    hs = [w["handle"] for w in dc.list_windows().get("windows", [])]
    assert STATE["np"] in hs, "Notepad is not in list_windows"
    return f"{len(hs)} windows listed"


def d_focus():
    import desktop_control as dc
    r = dc.focus_window(STATE["np"])
    assert r.get("ok"), r.get("error")
    return "focused" + ("" if r.get("verified", True) else " (not verified)")


def d_minimize():
    import desktop_control as dc
    r = dc.minimize_window(STATE["np"])
    assert r.get("ok"), r.get("error")
    return "minimized, verified" if r.get("verified") else "minimized (not verified)"


def d_restore():
    import desktop_control as dc
    r = dc.restore_window(STATE["np"])
    assert r.get("ok"), r.get("error")
    return "restored"


def d_maximize():
    import desktop_control as dc
    r = dc.maximize_window(STATE["np"])
    assert r.get("ok"), r.get("error")
    dc.restore_window(STATE["np"])
    return "maximized then restored"


def d_move_resize():
    import desktop_control as dc
    r1 = dc.move_window(STATE["np"], 200, 150)
    r2 = dc.resize_window(STATE["np"], 900, 600)
    assert r1.get("ok"), "move: " + str(r1.get("error"))
    assert r2.get("ok"), "resize: " + str(r2.get("error"))
    return "moved and resized"


def d_type():
    import desktop_control as dc
    dc.focus_window(STATE["np"])
    r = dc.type_text("Eva doctor test line", window_handle=STATE["np"])
    assert r.get("ok"), r.get("error")
    time.sleep(0.4)
    back = dc.read_window_text(STATE["np"])
    text = back.get("text", "") if isinstance(back, dict) else str(back)
    assert "Eva doctor test line" in text, f"typed text was not found when read back: {str(back)[:150]}"
    return "typed and read back"


def d_ui_tree():
    import desktop_control as dc
    t0 = time.monotonic()
    r = dc.ui_tree(STATE["np"])
    assert r.get("elements"), f"no elements: {r.get('error')}"
    return f"{len(r['elements'])} elements in {time.monotonic() - t0:.1f}s (isolated worker)"


def d_click():
    import desktop_control as dc
    w = next(x for x in __import__("pygetwindow").getAllWindows() if x._hWnd == STATE["np"])
    r = dc.click_coords(w.left + w.width // 2, w.top + w.height // 2, "doctor test")
    assert r.get("ok"), r.get("error")
    return f"delivered; screen_changed={r.get('screen_changed')}"


def d_close():
    import desktop_control as dc
    dc.focus_window(STATE["np"])
    dc.send_hotkey("ctrl+a", window_handle=STATE["np"])
    dc.send_hotkey("delete", window_handle=STATE["np"])       # leave it empty: no "save?" prompt
    r = dc.close_window(STATE["np"])
    assert r.get("ok"), r.get("error")
    if r.get("closed") is False:
        return "close delivered but the window is still open (probably a save prompt -- close it by hand)"
    return "closed"


def d_screen():
    import desktop_control as dc
    h1 = dc.screen_hash()
    assert h1 is not None, "could not capture the screen"
    return "screen captured"


def d_resolve():
    import app_resolver
    out = []
    for n in ("notepad", "calculator", "brave", "chrome", "edge", "word", "spotify"):
        r = app_resolver.resolve(n)
        out.append(f"{n}={'yes (' + r['via'] + ')' if r.get('kind') else 'no'}")
    return "; ".join(out)


# --------------------------------------------------------------------------
# browser
# --------------------------------------------------------------------------
def b_default():
    import browser_agent as ba
    r = ba.browser_open("https://example.com")
    assert r.get("ok"), f"open failed: {r}"
    page = ba._executor.submit(ba._ensure_browser).result()
    text = ba._executor.submit(lambda: page.inner_text("body")).result()
    assert "Example Domain" in text, "page text was not readable"
    return f"opened and read: {r.get('title')}"


def b_named(kind):
    def go():
        import app_resolver
        import browser_agent as ba
        if not app_resolver.resolve(kind).get("kind"):
            return f"{kind} is not installed (skipped)"
        r = ba.browser_open("https://example.com", kind)
        assert r.get("ok"), f"open failed: {r}"
        assert ba._browser_kind["k"] == kind, f"asked for {kind} but got the bundled browser"
        return f"{kind} driven successfully: {r.get('title')}"
    return go


def b_close():
    import browser_agent as ba
    r = ba._executor.submit(ba._close_browser_impl).result()
    assert r.get("ok"), r
    return "closed"


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------
def a_synth():
    import voice
    t0 = time.monotonic()
    text = ("Beginning of the test. If you can hear every word from the first to the last, "
            "the audio is working. This is the very end.")
    path = voice.synthesize(text)
    size = os.path.getsize(path)
    assert size > 3000, f"audio file is too small ({size} bytes)"
    STATE["mp3"] = path
    return f"synthesised {size // 1024} KB with the {voice.voice_source()} voice in {time.monotonic() - t0:.1f}s"


def a_play():
    import voice
    voice.prime_audio()
    time.sleep(0.5)
    t0 = time.monotonic()
    stopped = voice._play_interruptible(STATE["mp3"])
    dt = time.monotonic() - t0
    voice.shutdown_player()
    assert not stopped and dt > 2.0, f"playback ended too fast ({dt:.1f}s) -- nothing was played"
    STATE["played"] = True
    return f"played for {dt:.1f}s"


def a_mic():
    import speech_recognition as sr
    names = sr.Microphone.list_microphone_names()
    assert names, "no microphone found"
    with sr.Microphone() as src:
        assert src.stream is not None
    return f"{len(names)} input device(s); default opens fine"


def ask(question):
    if "--no-prompt" in ARGS:
        return None
    try:
        return input(question + " [y/n]: ").strip().lower().startswith("y")
    except EOFError:
        return None


def a_local_stt():
    import config
    import stt_local
    ok, detail = stt_local.health()
    if not ok:
        return (f"local Phonon server not running ({detail}); STT_BACKEND is "
                f"{config.STT_BACKEND!r}. Optional: run start_phonon.bat")
    # round trip with the clip made above if there is one, else a second of silence
    import io, wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    t0 = time.monotonic()
    text = stt_local.transcribe(buf.getvalue())
    assert text is not None, f"server is up but transcription failed: {stt_local._state['last_error']}"
    return (f"server healthy (model {detail}); 1 s of silence transcribed in "
            f"{time.monotonic() - t0:.2f}s as {text!r}; backend in config: {config.STT_BACKEND!r}")


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------
def s_memory_audit():
    import audit
    import memory
    d = tempfile.mkdtemp()
    m = memory.Memory(path=os.path.join(d, "m.db"))
    m.remember("doctor_key", "doctor value")
    assert "doctor" in str(m.recall("doctor")), "memory did not return what was stored"
    a = audit.AuditLog(path=os.path.join(d, "a.jsonl"))
    a.record(tool="doctor", ok=True)
    assert os.path.getsize(os.path.join(d, "a.jsonl")) > 0, "audit wrote nothing"
    return "memory and audit work"


def s_files():
    import backend_tools as bt
    p = os.path.join(tempfile.gettempdir(), "eva-doctor-note.txt")
    r = bt.write_file(p, "doctor", mode="overwrite")
    assert r.get("ok"), r.get("error")
    assert open(p, encoding="utf-8").read() == "doctor", "file content mismatch"
    os.remove(p)
    si = bt.system_info()
    assert si.get("ok", True), si
    return "write_file and system_info work"


def s_dir():
    d = os.path.join(os.path.expanduser("~"), ".eva")
    os.makedirs(d, exist_ok=True)
    probe = os.path.join(d, ".doctor")
    open(probe, "w").write("x")
    os.remove(probe)
    return f"{d} is writable"


# --------------------------------------------------------------------------
def main():
    print(__doc__.split("Options:")[0])
    started = time.time()

    print("--- setup ---")
    run("packages installed", check_imports)
    run("Eva modules import", check_eva_modules)
    run("Tool registry is complete", check_tool_registry)
    info("system", check_environment)
    info("API key source", check_key_source, 10)
    info("skills and connected services", check_skills_and_services, 90)
    run("reading a web page without the browser", check_web_reading, 80)
    info("graphics card and driver", check_gpu, 15)
    info("programs found by plain name", d_resolve, 40)
    run("~/.eva folder writable", s_dir)
    run("memory + audit log", s_memory_audit)
    run("files + system info", s_files)

    print("--- language model ---")
    if "--no-model" in ARGS:
        skip("model tool call", "--no-model")
    else:
        run("model tool call", check_model, 60)

    print("--- windows (drives a real Notepad) ---")
    if run("launch Notepad by name", d_launch, 45):
        run("appears in window list", d_listed)
        run("focus window", d_focus)
        run("minimize", d_minimize)
        run("restore", d_restore)
        run("maximize", d_maximize)
        run("move and resize", d_move_resize)
        run("type text and read it back", d_type, 60)
        run("read accessibility tree", d_ui_tree, 40)
        run("raw click", d_click)
        run("clear and close", d_close)
    else:
        skip("window checks", "Notepad did not start")
    run("screen capture", d_screen)

    print("--- browser ---")
    if "--no-browser" in ARGS:
        skip("browser", "--no-browser")
    else:
        if run("bundled browser opens and reads a page", b_default, 90):
            run("bundled browser closes", b_close, 30)
        for kind in ("brave", "chrome"):
            run(f"open in {kind} when asked", b_named(kind), 90)
            try:
                import browser_agent as ba
                ba._executor.submit(ba._close_browser_impl).result(timeout=20)
            except Exception:
                pass

    print("--- audio ---")
    if "--no-audio" in ARGS:
        skip("audio", "--no-audio")
    else:
        run("microphone available", a_mic, 20)
        info("local speech recognition (Phonon)", a_local_stt, 30)
        if run("speech synthesis (needs internet)", a_synth, 40):
            print("       Listen now: a test sentence is about to play.")
            if run("speech playback", a_play, 60):
                s = ask("Did you hear the FIRST word ('Beginning') clearly, not clipped?")
                e = ask("Did you hear the LAST words ('the very end') fully?")
                for label, val in (("audio start intact", s), ("audio end intact", e)):
                    if val is None:
                        skip(label, "not asked")
                    elif val:
                        record("PASS", label, 0.0)
                    else:
                        record("FAIL", label, 0.0, "the person heard it clipped")

    write_report(time.time() - started)


def write_report(seconds):
    fails = [r for r in RESULTS if r[0] == "FAIL"]
    lines = ["EVA DOCTOR REPORT", time.strftime("%Y-%m-%d %H:%M:%S"),
             f"{len(RESULTS)} items, {len(fails)} failed, {seconds:.0f}s", ""]
    for status, name, secs, detail in RESULTS:
        lines.append(f"[{status}] {name} ({secs:.1f}s)")
        if detail:
            lines += ["      " + d for d in str(detail).splitlines()[:14]]
    lines += ["", "FAILED:" if fails else "Nothing failed.", *[f"  - {r[1]}" for r in fails]]
    text = "\n".join(lines)
    try:
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        print(f"(could not write the report: {e})")
    print("\n" + "=" * 60)
    print(f"{len(fails)} of {len(RESULTS)} failed.  Report saved to:\n  {REPORT_PATH}")
    print("Please send that file back.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        write_report(0)
    finally:
        try:
            import voice
            voice.shutdown_player()
        except Exception:
            pass
    if "--no-prompt" not in ARGS:
        try:
            input("\nPress Enter to close.")
        except EOFError:
            pass
    os._exit(0)      # do not let a stuck helper thread keep the window open
