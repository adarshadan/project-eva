"""Her own settings (settings_store.py): open the Settings window, or change one
setting when asked ("talk less", "make the bubbles green", "be smaller").
What decides safety (what asks first, the API key) is not a setting here."""
import settings_store as store
from core.effects import Effect as E, Surface as S
from core.registry import STR, register


def change_setting(key, value):
    key = str(key or "").strip().upper()
    res = store.save({key: value})
    if res["errors"]:
        return {"ok": False, "error": res["errors"][0],
                "settings": [s["key"] for s in store.SETTINGS]}
    spec = store.BY_KEY[key]
    out = {"ok": True, "setting": spec["label"], "value": getattr(__import__("config"), key)}
    if res["restart"]:
        out["note"] = "saved; it takes effect the next time Eva starts (tray > Restart Eva applies it now)"
    elif not res["changed"]:
        out["note"] = "it was already set that way"
    return out


def list_settings():
    cur = store.current()
    return {"ok": True, "settings": [
        {"key": s["key"], "label": s["label"], "group": s["group"], "value": cur.get(s["key"]),
         **({"choices": s["choices"]} if s["choices"] else {}),
         **({"range": [s["min"], s["max"]]} if s["min"] is not None else {}),
         **({} if s["live"] else {"needs_restart": True})} for s in store.SETTINGS]}


_KEYS = [s["key"] for s in store.SETTINGS]
register("open_settings", "Open the Settings window (size, gesture speed, voice, how much she says aloud, bubble "
         "colours, initiative, hotkeys).", surface=S.SELF, effect=E.OBSERVE, embodied=False)
register("change_setting", "Change one of your own settings when the person asks: e.g. 'talk less' -> "
         "LONG_REPLY_CHARS lower; 'make the bubbles green' -> BUBBLE_COLOURS green; 'be smaller' -> AVATAR_SCALE; "
         "'move slower' -> ANIMATION_SPEED lower; 'use an Indian voice' -> TTS_VOICE en-IN-NeerjaNeural; 'offer my "
         "usual routines' -> PREDICTIVE_HELP true. Call list_settings first if unsure of a value. Safety settings "
         "(what asks first) cannot be changed this way.",
         {"key": STR(enum=_KEYS), "value": STR("The new value: true/false, a number, or one of the choices.")},
         ["key", "value"], surface=S.SELF, effect=E.MODIFY, fn=change_setting, embodied=False,
         describe=lambda a: f"Changing {a.get('key', 'a setting')}")
register("list_settings", "Your settings and their current values, with the allowed choices.",
         surface=S.SELF, effect=E.OBSERVE, fn=list_settings, embodied=False)
register("restart_as_administrator", "Restart yourself as administrator, when the person asks or agrees, so you can "
         "act on windows that run as administrator (an elevated installer, WizTree, a game launcher). Windows asks "
         "them to approve; say so first, because you go away for a few seconds and the conversation starts fresh.",
         surface=S.SYSTEM, effect=E.MODIFY, embodied=False)
