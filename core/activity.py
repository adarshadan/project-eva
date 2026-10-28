"""Eva core -- what she looks like she is doing.

Turns the skill or tool in play into one of the avatar's activity poses
(core/web/avatar_3d.html's ACTIVITIES), so her body matches the task: reading
while she reads a page, writing while she types, organising while she moves
files, thinking while she reasons or recalls a skill. The pose is continuous
and eased, set here and held until the next one -- not a clip per call.

Two levels:
  for_tool(name)   the action running right now (from the tool's verb/surface)
  for_skill(...)   the task as a whole (from a loaded skill's name/category),
                   held between the discrete actions a skill performs
"""
import capabilities
from core.effects import Surface

ACTIVITIES = ("idle", "listening", "thinking", "reading", "searching", "writing",
              "browsing", "organizing", "presenting", "watching", "playing")

# The tool's verb is the main signal (it already encodes intent in the manifest).
_VERB = {
    "read": "reading", "look": "reading", "scroll": "reading",
    "search": "searching",
    "type": "writing", "write": "writing",
    "run": "working", "work": "working",
    "tidy": "organizing", "arrange": "organizing", "close": "organizing",
    "open": "browsing", "reach": "working", "greet": "working",
    "point": "presenting", "send": "presenting",
}
# "working" isn't its own pose (ACTIVITIES has no "working"); the JS maps it to
# the writing/busy pose. Kept distinct here so the status reads sensibly.
_SURFACE = {Surface.BROWSER: "browsing", Surface.FILES: "reading"}
_SKILL_KEYWORDS = [
    (("research", "news", "search", "arxiv", "osint", "competitor", "investigat"), "searching"),
    (("write", "writer", "document", "docx", "essay", "draft", "email", "report", "blog",
      "pptx", "powerpoint", "slide", "excel", "xlsx", "spreadsheet", "finance", "model",
      "design", "resume", "humaniz", "copy", "content"), "writing"),
    (("tidy", "organis", "organiz", "clean", "sort", "file", "folder"), "organizing"),
    (("read", "summar", "pdf", "transcript", "youtube", "feed", "brief", "review", "triage"), "reading"),
    (("debug", "code", "test", "build", "deploy", "mcp", "script", "automat"), "working"),
    (("present", "pitch", "demo", "teach", "explain"), "presenting"),
]


def for_tool(name: str):
    """The activity for a running tool, or None to leave the base activity."""
    spec = capabilities.get(name)
    if spec is None:
        return None
    if spec.verb in _VERB:
        return _VERB[spec.verb]
    return _SURFACE.get(getattr(spec, "surface", None))


def for_skill(skill_name: str, category: str = ""):
    """A task-level activity from a loaded skill's name and category."""
    text = f"{skill_name} {category}".lower()
    for words, act in _SKILL_KEYWORDS:
        if any(w in text for w in words):
            return act
    return "thinking"
