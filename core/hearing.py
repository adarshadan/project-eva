"""Eva -- what the microphone heard, made honest for the model.

Speech recognition mishears, and the model must know HOW sure it was and what
else it might have been, to understand the person the way a person would.
eva.log 2026-10-11: "click on the video deposit" (to pause it), "increase the
schooling speed" (scrolling), "Tyres too slow" (it's too slow), "open model
game" (Wordle), "Dewas top" (Eva stop), "Japan's dear City" (Deer), "no loss"
(laws), and the person spelling "w o r d l e" letter by letter.

voice.py records the recogniser's alternatives and confidence here
(note_alternatives); annotate() adds one bracketed line under the heard text:

  open model game on the browser
  [speech-to-text, may be misheard: confidence 0.58; also heard as "open wordle game on the browser"]

and joins spelled-out letters ("w o r d l e" -> spelled: WORDLE). The
prompt (core/prompt.py, HEARD SPEECH) tells the model what to do with it.
"""
import re
import threading
import time

_LAST = {"text": None, "alternatives": [], "confidence": None, "at": 0.0}
_lock = threading.Lock()
SURE = 0.85                      # at or above this the line says nothing about doubt
SPELLED = re.compile(r"(?<![\w'])((?:[a-z]\s+){2,}[a-z])(?![\w'])", re.I)


def note_alternatives(text, alternatives=(), confidence=None):
    with _lock:
        _LAST.update(text=str(text or ""), alternatives=[str(a) for a in alternatives if a and a != text][:3],
                     confidence=confidence, at=time.time())


def spelled_words(text):
    return ["".join(m.group(1).split()).upper() for m in SPELLED.finditer(str(text or ""))]


def annotate(text, now=None):
    """The heard text plus what the model should know about how it was heard."""
    now = now or time.time()
    parts = []
    with _lock:
        last = dict(_LAST)
    if last["text"] and last["text"].strip().lower() == str(text).strip().lower() and now - last["at"] < 30:
        conf = last["confidence"]
        if conf is not None and conf < SURE:
            parts.append(f"confidence {conf:.2f}")
        if last["alternatives"]:
            parts.append("also heard as " + " | ".join(f'"{a}"' for a in last["alternatives"]))
    spelled = spelled_words(text)
    if spelled:
        parts.append("spelled out: " + ", ".join(spelled))
    if not parts:
        return text
    return f"{text}\n[speech-to-text, may be misheard: {'; '.join(parts)}]"
