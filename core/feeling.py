"""Eva core -- how she feels, so her whole body shows it.

2026-10-28: "we need her to feel more alive, excited to work, her actions not limited to what is
programmatically configured but dynamic, responsive". Her gestures were a fixed set played on cue; nothing
she felt changed HOW she stood or moved. Now she has a continuous feeling -- energy (0 sleepy .. 1 buzzing)
and mood (-1 low .. +1 bright) -- that shapes everything in the avatar page (posture, a bounce when excited,
a drooping head when sorry, her smile, how often she looks around), plus short generated reactions
(eager, delight, oops, curious, proud) whose size and timing vary every time.

Where feelings come from:
  * what is happening: being spoken to (eager), working (keen), a task done (delight, bigger for a bigger
    job), a task that failed (oops), not confirmed (curious);
  * her own choice: a reply may begin with one feeling in square brackets ("[proud] All three files are
    renamed."). It is taken out before the reply is shown or spoken (take()), and her body shows it. Any
    plain word is accepted; known words have tuned values, others a mild default.

This module only decides; the avatar is driven through two callables (avatar_ui: set_affect, emote).
"""
import random
import re

# word -> (energy, mood, reaction or None, reaction size)
LEXICON = {
    "happy": (0.65, 0.7, "delight", 0.5), "excited": (0.92, 0.8, "delight", 0.9),
    "delighted": (0.8, 0.85, "delight", 0.8), "glad": (0.6, 0.6, None, 0), "joyful": (0.85, 0.85, "delight", 0.8),
    "proud": (0.7, 0.8, "proud", 0.7), "curious": (0.7, 0.3, "curious", 0.6),
    "interested": (0.65, 0.3, "curious", 0.4), "thoughtful": (0.4, 0.1, "curious", 0.3),
    "calm": (0.35, 0.3, None, 0), "relaxed": (0.3, 0.4, None, 0), "content": (0.4, 0.5, None, 0),
    "amused": (0.65, 0.6, "delight", 0.4), "playful": (0.85, 0.6, "delight", 0.6),
    "determined": (0.8, 0.2, "eager", 0.6), "eager": (0.85, 0.45, "eager", 0.8), "keen": (0.75, 0.4, "eager", 0.5),
    "focused": (0.6, 0.1, None, 0), "sorry": (0.35, -0.45, "oops", 0.5), "sad": (0.25, -0.6, "oops", 0.3),
    "embarrassed": (0.45, -0.3, "oops", 0.6), "confused": (0.5, -0.2, "curious", 0.5),
    "worried": (0.55, -0.4, None, 0), "surprised": (0.8, 0.2, "curious", 0.8),
    "grateful": (0.55, 0.75, "delight", 0.4), "thankful": (0.55, 0.75, "delight", 0.4),
    "sleepy": (0.15, 0.1, None, 0), "tired": (0.2, -0.1, None, 0), "frustrated": (0.65, -0.5, "oops", 0.6),
    "relieved": (0.45, 0.6, "delight", 0.3), "shy": (0.4, 0.3, "oops", 0.3), "confident": (0.7, 0.6, "proud", 0.5),
}
DEFAULT = (0.6, 0.25, None, 0)
NOT_FEELINGS = {"voice", "chat", "system", "observation", "scheduled", "text", "note", "image", "link", "done",
                "read aloud", "aloud", "read out", "read it aloud", "say all", "say it all"}
_TAG = re.compile(r"^\s*\[\s*(?:feeling\s*[:=]\s*)?([A-Za-z][A-Za-z \-]{1,24})\s*\]\s*")


def take(text):
    """(feeling or None, text without it): a leading "[proud]" / "[feeling: proud]" is hers to show."""
    if not text:
        return None, text
    m = _TAG.match(str(text))
    if not m:
        return None, text
    word = m.group(1).strip().lower()
    if word in NOT_FEELINGS or len(word.split()) > 2:
        return None, text
    return word, str(text)[m.end():]


class Feeling:
    """Turns events into the body's feeling. affect(energy, mood, hold_seconds); emote(name, size)."""

    def __init__(self, affect, emote, rng=None):
        self._affect, self._emote = affect, emote
        self.rng = rng or random.Random()
        self._working = False

    def _do(self, energy, mood, hold, reaction=None, size=0.0):
        try:
            self._affect(round(energy, 2), round(mood, 2), hold)
            if reaction and size > 0:
                self._emote(reaction, round(min(1.0, size * self.rng.uniform(0.85, 1.15)), 2))
        except Exception:
            pass                                  # a feeling is decoration: never break the work

    def spoken_to(self):
        self._working = False
        self._do(0.75, 0.35, 15, "eager", self.rng.uniform(0.45, 0.8))

    def working(self):
        if not self._working:
            self._working = True
            self._do(0.72, 0.3, 30)

    def finished(self, card):
        self._working = False
        status = (card or {}).get("status")
        steps = len((card or {}).get("steps") or [])
        if status == "done":
            self._do(0.82, 0.7, 8, "delight", 0.45 + 0.06 * min(steps, 9))
        elif status == "not_confirmed":
            self._do(0.6, 0.15, 6, "curious", 0.4)
        elif status == "failed":
            self._do(0.4, -0.4, 10, "oops", 0.7)

    def felt(self, word):
        energy, mood, reaction, size = LEXICON.get(str(word or "").lower(), DEFAULT)
        self._do(energy, mood, 12, reaction, size)
