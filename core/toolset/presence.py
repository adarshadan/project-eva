"""Eva's own presence (movement, gestures) and her memory. These need live
objects -- the avatar widget, the memory store -- so they are declared here and
bound at startup (core/app wiring). Speech is deliberately NOT a tool: what
Eva writes as her reply is spoken and shown automatically."""
from core.effects import Effect as E, Surface as S
from core.registry import BOOL, INT, STR, register

# Gesture names the model may ask for. Unknown names are mapped to the nearest
# real gesture by the implementation (see core/presence_impl.py) rather than
# refused, so "do a moonwalk" still gets a dance and an honest note.
GESTURES = ["wave", "hello", "dance", "twirl", "peace", "pose", "stretch", "finger_gun", "squat", "bow",
            "nod", "think", "shrug", "cheer", "point", "beckon", "click", "tidy", "arrange", "work",
            "fly_loop", "nap", "walk_taskbar", "jump",
            "clap", "facepalm", "heart", "shh", "check_watch", "impatient", "scratch_head", "peer", "carry",
            "paper_plane", "wipe_brow", "fist_pump", "tada", "salute", "tap_head", "sneeze", "hum", "shoo",
            "rewind", "checklist", "yawn", "look_around"]

register("play_animation", "Play a gesture or animation: " + ", ".join(GESTURES) + ". Any other name is "
         "mapped to the closest one and the result says which played. walk_taskbar strolls along "
         "the taskbar; jump really jumps (times = how many). Use freely when asked; unprompted only "
         "when the person is idle.",
         {"name": STR(), "loop": BOOL("Keep repeating until stopped. Default false."),
          "times": INT("jump only: how many jumps, 1-5. Default 1.")}, ["name"],
         surface=S.SELF, effect=E.OBSERVE, embodied=False)
register("stop_animation", "Stop the current animation and return to idle.",
         surface=S.SELF, effect=E.OBSERVE, embodied=False)
register("walk_to_screen", "Walk yourself to a screen position and stand there (x, y = screen pixels at "
         "your feet). This is how you go somewhere: 'come here', 'go to the right side'. Use "
         "get_screen_size to find sides and corners. You stay where you are sent. To SIT on a "
         "window use sit_on_window instead.",
         {"x": INT(), "y": INT()}, ["x", "y"], surface=S.SELF, effect=E.OBSERVE, embodied=False)
register("sit_on_window", "Sit on top of a window, on its title bar, legs over the edge -- for 'sit on "
         "that window', 'go sit on Notepad'. You ride along when it moves, and hop down if it is "
         "minimised, maximised or closed. Do not use walk_to_screen for this: walking only stands.",
         {"window_handle": INT(), "spot": STR("Where along its top: left, middle or right. Default middle.",
                                               enum=["left", "middle", "right"])}, ["window_handle"],
         surface=S.SELF, effect=E.OBSERVE, embodied=False)
register("sit_down", "Sit down where you are (on the taskbar, or on the window edge you stand on).",
         surface=S.SELF, effect=E.OBSERVE, embodied=False)
register("stand_up", "Stand up (and come down to the taskbar if you were sitting on a window).",
         {"come_down": BOOL("Also come down to the taskbar. Default false.")},
         surface=S.SELF, effect=E.OBSERVE, embodied=False)
register("go_home", "Walk back to your home spot (where you started, or where the person last put you).",
         surface=S.SELF, effect=E.OBSERVE, embodied=False)

# ---- memory -------------------------------------------------------------------------
register("remember", "Keep a short FACT about the person or their machine across sessions (a "
         "preference, a folder, how they like things). Notes that read like instructions or "
         "permissions, and credentials, are refused.",
         {"key": STR("A short label."), "value": STR(),
          "category": STR(enum=["person", "preference", "environment", "note"])}, ["key", "value"],
         surface=S.SYSTEM, effect=E.MODIFY, verb="write", describe=lambda a: "Making a note")
register("recall", "Look up what you remember (facts and past tasks) about a topic.",
         {"query": STR(), "limit": INT()},
         surface=S.SYSTEM, effect=E.OBSERVE, verb="look", describe=lambda a: "Thinking back")
register("forget", "Forget one remembered fact by its key.", {"key": STR()}, ["key"],
         surface=S.SYSTEM, effect=E.MODIFY, verb="write")
register("forget_all", "Erase everything remembered. Asks the person first.",
         surface=S.SYSTEM, effect=E.DESTROY, verb="write")
