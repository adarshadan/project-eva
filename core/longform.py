"""Eva core -- what is too long to say is shown, not read out.

eva.log 2026-10-10: asked for a 1,500-word passage, Eva SPOKE all of it; a
search result came back as a spoken paragraph of shop names and ratings; a
verification message was read out as a numbered list. The prompt already said
"one or two short sentences", and the person had told her, in so many words,
"anything more than 100 characters, write it in a Notepad". Neither was
enough, because whether to obey was left to the model.

So the framework decides. Every reply passes through shape():
  * trailing filler ("Let me know if you need anything else!") is removed;
  * if what is left is longer than config.LONG_REPLY_CHARS, she says one
    short line, and the full text goes to her message bubble and the chat
    window (2026-10-11: "she does [not] need to put the long responses in
    notepad but instead put in the bubble ... available to reference in the
    chat window"). config.LONG_REPLY_TARGET = "notepad" restores the old
    behaviour: the text is written to Documents\\Eva\\<date time> - <first
    words>.txt and opened in Notepad.
Lines said WHILE working are held to the same limit: a long one is cut to its
first sentence, or not spoken at all.
"""
import os
import re
import time

import config
from logger import get_logger

log = get_logger(__name__)

# Closing pleasantries that add nothing when spoken, at the END of a reply.
_FILLER = re.compile(
    r"(?:\s*(?:"
    r"let me know (?:if|whether|what|when|how)[^.!?]*[.!?]?|"
    r"(?:is there )?anything else (?:i can|you(?:'d| would) like|you need)[^.!?]*[.!?]?|"
    r"feel free to [^.!?]*[.!?]?|"
    r"(?:just )?(?:tell|ask) me if [^.!?]*[.!?]?|"
    r"(?:i'?m )?(?:happy|here) to help[^.!?]*[.!?]?|"
    r"hope (?:this|that) helps[^.!?]*[.!?]?"
    r"))+\s*$", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def strip_filler(text: str) -> str:
    if not text:
        return text
    out = _FILLER.sub("", text).strip()
    return out or text.strip()        # never strip a reply down to nothing


def first_sentence(text: str) -> str:
    return _SENTENCE_END.split((text or "").strip(), maxsplit=1)[0].strip()


_ALOUD = re.compile(r"^\s*\[\s*(?:read\s+(?:it\s+)?aloud|read\s+out|aloud|say\s+(?:it\s+)?all)\s*\]\s*", re.I)


def take_aloud(text):
    """(True, text without it) for a reply that begins "[read aloud]": she chose to speak all of it.
    2026-10-28 09:58-10:00: "I am away from my laptop ... I need to hear" -- three times she answered with
    the full text in her bubble and said only "Here you go.", believing she was speaking it all."""
    if not text:
        return False, text
    m = _ALOUD.match(str(text))
    return (True, str(text)[m.end():]) if m else (False, text)


def is_conversational(text: str) -> bool:
    """A spoken answer, not content: at most two sentences, no list, no line
    breaks, within SPOKEN_MAX_CHARS. eva.log 2026-10-11 13:16: a two-sentence
    reply to the person ("I overwrote the current tab... shall I open a new
    one?") went to Notepad and stacked up windows; the rule was meant for
    paragraphs, lists and essays."""
    t = (text or "").strip()
    if not t or len(t) > int(getattr(config, "SPOKEN_MAX_CHARS", 240) or 0):
        return False
    if "\n" in t or re.search(r"(^|\s)(\d+[.)]|[-*•])\s", t):
        return False
    sentences = [x for x in _SENTENCE_END.split(t) if x.strip()]
    return len(sentences) <= 2


def limit() -> int:
    return int(getattr(config, "LONG_REPLY_CHARS", 100) or 0)


def target() -> str:
    return str(getattr(config, "LONG_REPLY_TARGET", "bubble") or "bubble").strip().lower()


SPOKEN_ANSWER_MAX = 600          # a reply up to this long is an ANSWER: its opening is said, not "Here you go."


def spoken_line(text: str) -> str:
    """What she says aloud about a long reply that is shown.

    A short prose answer (no list, up to SPOKEN_ANSWER_MAX) is an answer to a
    question: she says its first sentence or two (eva.log 2026-10-11 22:52: "if I
    run you as administrator will that work" got only "Here you go."). Otherwise
    the first sentence when it is a short lead-in ("Here are three options:"),
    else a plain hand-over."""
    t = text.strip()
    spoken_max = int(getattr(config, "SPOKEN_MAX_CHARS", 240) or 240)
    first_para = t.split("\n")[0].strip()
    if len(t) <= SPOKEN_ANSWER_MAX and not re.search(r"(^|\n)\s*(\d+[.)]|[-*•#])\s", t) \
            and not first_para.endswith(":"):
        sentences = [x.strip() for x in _SENTENCE_END.split(first_para) if x.strip()]
        said = ""
        for sen in sentences[:2]:
            if len(said) + len(sen) + 1 > spoken_max:
                break
            said = (said + " " + sen).strip()
        if said and len(said.split()) >= 3:
            return said if said[-1:] in ".!?" else said + "."
    first = first_sentence(first_para).rstrip(":").strip()
    cap = limit()
    if first and first != text.strip() and len(first) <= min(cap, 90) and "\n" not in first \
            and len(first.split()) >= 2 and not re.match(r"^(\d+[.)]|[-*•#])(\s|$)", first):
        return first if first[-1:] in ".!?" else first + "."
    return "Here you go."


def _title(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9']+", text)[:6]
    return (" ".join(words) or "Eva notes")[:50]


def default_folder() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "Eva")


class LongForm:
    def __init__(self, folder=None, opener=None, clock=time.localtime):
        self.folder = folder or default_folder()
        self._open = opener          # (path) -> None; default: Notepad via launch_app
        self._clock = clock

    def _open_file(self, path):
        if self._open is not None:
            return self._open(path)
        import desktop_control as dc
        return dc.launch_app("notepad", path)

    def save(self, text: str) -> str:
        os.makedirs(self.folder, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H%M", self._clock())
        name = re.sub(r'[<>:"/\\|?*]', "", f"{stamp} - {_title(text)}").strip() or stamp
        path, n = os.path.join(self.folder, name + ".txt"), 2
        while os.path.exists(path):
            path, n = os.path.join(self.folder, f"{name} ({n}).txt"), n + 1
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.strip() + "\n")
        return path

    def shape(self, reply: str):
        """The line she SAYS (see shape_ex for what is shown)."""
        return self.shape_ex(reply)[0]

    def shape_ex(self, reply: str, read_aloud: bool = False):
        """(said, shown). A short reply is both. A long one: she says one
        short line and the bubble / chat window show the whole text -- or,
        with LONG_REPLY_TARGET = "notepad", it is saved and opened in
        Notepad and both are the one line about that."""
        if not reply:
            return reply, reply
        text = strip_filler(reply)
        if read_aloud:                     # [read aloud]: the person needs to HEAR it (2026-10-28 live)
            log.info(f"Long reply ({len(text)} chars) read aloud in full, as she chose")
            return text, text
        cap = limit()
        if cap <= 0 or len(text) <= cap or is_conversational(text):
            return text, text
        if target() != "notepad":
            log.info(f"Long reply ({len(text)} chars) shown in the bubble and the chat window")
            return spoken_line(text), text
        line = self._to_notepad(text)
        return line, line

    def _to_notepad(self, text):
        try:
            path = self.save(text)
            opened = self._open_file(path)
            ok = not (isinstance(opened, dict) and opened.get("ok") is False)
            log.info(f"Long reply ({len(text)} chars) written to {path}; opened={ok}")
            return "I've put that in Notepad for you." if ok else f"I've saved that to {os.path.basename(path)} in Documents, Eva."
        except Exception:
            log.exception("Could not write the long reply to a file -- saying the first sentence")
            return first_sentence(text)

    def interim(self, line: str):
        """A line said while working: within the limit, or its first sentence
        if that fits, or nothing."""
        if not line:
            return None
        text = strip_filler(line)
        cap = limit()
        if cap <= 0 or len(text) <= cap:
            return text
        first = first_sentence(text)
        return first if len(first) <= cap else None
