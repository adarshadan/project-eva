"""
Eva — learn-a-workflow-then-automate-it.
Recording captures the same action calls Eva already makes through desktop_control,
so replay re-resolves elements live instead of trusting stale coordinates.
"""
import time
import threading
import desktop_control as dc
from logger import get_logger

log = get_logger(__name__)

_active_name = None
_active_steps = []
_saved = {}  # name -> list of steps


def start_recording(name: str):
    global _active_name, _active_steps
    _active_name, _active_steps = name, []
    return {"ok": True, "name": name}


def stop_recording():
    global _active_name
    if _active_name is None:
        return {"ok": False, "error": "not recording"}
    _saved[_active_name] = list(_active_steps)
    saved_name = _active_name
    _active_name = None
    return {"ok": True, "name": saved_name, "steps": len(_saved[saved_name])}


def list_recordings():
    return {"recordings": list(_saved.keys())}


def record_step(step: dict):
    """Called by core/agent.py's dispatcher after every gated action, only while recording."""
    if _active_name is not None:
        _active_steps.append(step)


# Replay is the other thing long enough that waiting for a tool-round
# boundary is no answer: a twenty-step recording drives the real mouse and
# keyboard for as long as it takes, and "stop" could not touch it. Same
# event the browser agent observes.
_abort = threading.Event()


def request_abort():
    _abort.set()


def clear_abort():
    _abort.clear()


# Recorded step kinds -> the tool that performs them. Replay goes through
# the SAME dispatch as a live call, one tool call per step, so every step is
# assessed, approved if it must be, performed (she walks to it, looks, reacts)
# and audited exactly as if the model had asked for it just now. Before, a
# recording called the desktop primitives directly: motionless, and able to
# replay a select-all-delete without anyone being asked.
_STEP_TOOLS = {
    "click_element": lambda st: ("click_element", {"window_handle": st["window_handle"], "element_id": st["element_id"]}),
    "click_coords": lambda st: ("click_coords", {"x": st["x"], "y": st["y"], "reason_no_element": "replayed recording"}),
    "right_click_coords": lambda st: ("right_click_coords", {"x": st["x"], "y": st["y"], "reason_no_element": "replayed recording"}),
    "double_click_coords": lambda st: ("double_click_coords", {"x": st["x"], "y": st["y"], "reason_no_element": "replayed recording"}),
    "drag": lambda st: ("drag_and_drop", {"from_x": st["from_x"], "from_y": st["from_y"], "to_x": st["to_x"],
                                          "to_y": st["to_y"], "reason_no_element": "replayed recording"}),
    "type_text": lambda st: ("type_text", {"text": st["text"]}),
    "type_into_element": lambda st: ("type_into_element", {"window_handle": st["window_handle"],
                                                            "element_id": st["element_id"], "text": st["text"]}),
    "hotkey": lambda st: ("send_hotkey", {"keys": st["keys"]}),
}


def run_recording(name: str, params: dict = None, step_runner=None):
    """Replay a saved recording. step_runner(tool, args) -> result runs one
    step through the normal dispatch; without one (headless use) the steps
    call the desktop directly, as they always did."""
    if name not in _saved:
        return {"ok": False, "error": "no such recording"}
    steps = _saved[name]
    for index, step in enumerate(steps):
        if _abort.is_set():
            log.info(f"Recording {name!r} aborted at step {index + 1}/{len(steps)}.")
            return {"ok": False, "aborted": True, "completed_steps": index,
                    "total_steps": len(steps),
                    "error": f"stopped at the person's request after {index} of "
                             f"{len(steps)} steps -- the task is half-done, say so"}
        builder = _STEP_TOOLS.get(step["type"])
        if builder is None:
            log.warning(f"Recording {name!r}: unknown step type {step['type']!r} at step {index + 1} -- skipped")
            continue
        tool, args = builder(step)
        result = step_runner(tool, args) if step_runner else _run_directly(tool, args)
        if isinstance(result, dict) and (result.get("ok") is False or "error" in result) \
                and result.get("ok") is not True:
            # A replay that carries on past a failed step keeps clicking
            # into a screen that is no longer the one it was recorded on.
            return {"ok": False, "name": name, "failed_step": index + 1, "total_steps": len(steps),
                    "denied": bool(result.get("denied")),
                    "error": f"step {index + 1} of {len(steps)} ({tool}) did not work: "
                             f"{str(result.get('error', 'unknown'))[:160]}. Stopped there."}
        time.sleep(0.25)
    return {"ok": True, "name": name, "steps_run": len(steps)}


def _run_directly(tool, args):
    fn = getattr(dc, tool)
    return fn(**args)
