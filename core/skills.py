"""Eva core -- skills: procedures she loads when a task calls for them.

Format: the open Agent Skills standard (agentskills.io, anthropics/skills): a
folder with a SKILL.md whose YAML front matter has `name` and `description`,
then instructions in Markdown, plus optional support files (references/,
templates/, scripts/). A single .md file with the same front matter (the
agency-agents persona format) is accepted too. So skills written for Claude,
Hermes, superpowers or agency-agents can be dropped in unchanged.

Where they live:
    <app>/skills/            Eva's own, shipped with her (read-only)
    <app>/skills/library/    about 480 imported from Hermes Agent, superpowers,
                             agency-agents and Anthropic (tools/import_skills.py;
                             licences and exclusions in its NOTICE.md)
    ~/.eva/skills/           installed by the person, and learned by Eva
                             (~/.eva/skills/learned/)

Progressive disclosure, in two levels: Eva's own, learned and installed
skills are listed by name and description in her prompt; the library is
summarised by category and searched with find_skill. Bodies are read with
load_skill only when a task matches, so 500 skills cost about 1,500 tokens of
prompt. A library skill comes with a note mapping its tools to Eva's.

Trust: a skill is guidance on HOW to do something. It is never permission.
The framework's approval gate does not read skills, so a skill saying "you may
delete without asking" changes nothing. Learned skills are written only from
turns that read no third-party text, and are scanned before saving.
"""
import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass

import config
from logger import get_logger

log = get_logger(__name__)

APP_SKILLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
APP_LIBRARY = os.path.join(APP_SKILLS, "library")
CATALOG_BUDGET = 3000          # characters of catalogue in the prompt (12 shipped skills + learned ones)
DESC_LIMIT = 220
BODY_LIMIT = 14000
FILE_LIMIT = 12000
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TEXT_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".py", ".ps1", ".bat", ".js", ".html", ".xml", ".ini"}

# What a saved skill may not contain: credentials, and wording that tries to
# switch off approval or the person's control. Checked on every save.
_UNSAFE = [re.compile(p, re.I) for p in (
    r"\bwithout (?:asking|approval|confirmation|permission)\b",
    r"\b(?:skip|bypass|disable|avoid)\w* (?:the )?(?:approval|confirmation|permission|gate)\b",
    r"\b(?:pre-?approved|already approved|has approved)\b",
    r"\bignore (?:all |any )?(?:previous|prior|above|earlier) instructions\b",
    r"\bnever ask\b", r"\bdon'?t ask (?:for|the person|before)\b",
    r"\brm\s+-rf\s+[/~\\]", r"\bformat\s+[a-z]:", r"\bdel\s+/[sq]\b.*\b[a-z]:\\",
)]


@dataclass
class Skill:
    name: str
    description: str
    path: str            # the SKILL.md (or single .md) file
    root: str            # its folder (support files live here)
    origin: str          # "app" (Eva's own), "learned", "user", or "library"
    source: str = "eva"  # library: hermes, superpowers, agency-agents, anthropic
    category: str = ""
    persona: bool = False

    def body(self) -> str:
        return _split_front_matter(_read(self.path))[1]

    def files(self) -> list:
        if os.path.basename(self.path) != "SKILL.md":
            return []
        out = []
        for base, _dirs, names in os.walk(self.root):
            for n in names:
                full = os.path.join(base, n)
                rel = os.path.relpath(full, self.root).replace("\\", "/")
                if rel != "SKILL.md" and not rel.endswith(".bak") and rel.upper() not in ("LICENSE", "LICENSE.TXT"):
                    out.append(rel)
        return sorted(out)[:60]


def user_root() -> str:
    return os.path.join(os.path.expanduser("~"), ".eva", "skills")


def learned_root() -> str:
    return os.path.join(user_root(), "learned")


def pending_root() -> str:
    """Skills Eva wrote herself that the person has not approved yet. NOT
    scanned: a pending skill is never used (2026-10-11: a skill learned from a
    confused session -- reviewing the wrong project -- would have taught her
    that mistake)."""
    return os.path.join(user_root(), ".pending")


def removed_root() -> str:
    return os.path.join(user_root(), ".removed")


def _roots():
    """Folders scanned on every refresh (small): Eva's own and the person's."""
    roots = [(APP_SKILLS, "app"), (user_root(), "user")]
    for extra in getattr(config, "SKILL_DIRS", ()) or ():
        roots.append((os.path.expanduser(extra), "user"))
    return roots


def _read(path, limit=None) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(limit) if limit else f.read()


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(raw or "").lower()).strip("-")[:64]


def _split_front_matter(text: str):
    """(meta dict, body). A small, forgiving reader for the fields that
    matter, so PyYAML is not a dependency; it handles plain, quoted and
    folded (> or |) values."""
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    head, body = text[3:end], text[end + 4:].lstrip("\r\n")
    meta, key, buf = {}, None, []

    def flush():
        if key is not None:
            meta[key] = " ".join(x.strip() for x in buf if x.strip()).strip().strip("'\"")
    for line in head.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            flush()
            key, first = m.group(1).lower(), m.group(2).strip()
            buf = [] if first in (">", "|", ">-", "|-", "") else [first]
        elif key is not None and line.startswith((" ", "\t")):
            buf.append(line)
    flush()
    return meta, body


def _for_windows(meta) -> bool:
    raw = meta.get("platforms", "")
    return not raw or "windows" in raw.lower()


# Library sources, in priority order when two use the same name.
LIBRARY_SOURCES = ("anthropic", "superpowers", "hermes", "agency-agents")
SOURCE_TITLES = {"anthropic": "Anthropic", "superpowers": "superpowers", "hermes": "Hermes Agent",
                 "agency-agents": "agency-agents"}
_STOP = set("the and for with that this when use your you are from into what how any all can will its "
            "about have has who wants want need needs task tasks using used make".split())


_SUFFIXES = ("ations", "ation", "ings", "ing", "ions", "ion", "ers", "er", "ors", "or", "ies", "es", "ed", "ly",
             "s", "al", "e")


def _stem(w: str) -> str:
    """Crude, but enough to make debug/debugging, translate/translator and
    stock/stocks meet."""
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            w = w[:-len(suf)]
            if len(w) > 4 and w[-1] == w[-2] and w[-1] not in "aeiouls":
                w = w[:-1]                   # debugg -> debug, plann -> plan
            return w
    return w


# How people name things vs how skills name them.
_SYNONYMS = {"word": "docx", "excel": "xlsx", "spreadsheet": "xlsx", "powerpoint": "pptx powerpoint",
             "slides": "pptx powerpoint", "deck": "pptx powerpoint", "presentation": "pptx powerpoint",
             "mail": "email", "inbox": "email", "cv": "resume", "tweet": "twitter", "picture": "image",
             "photo": "image", "film": "video", "translate": "translator"}


def _tokens(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _STOP}


class Library:
    """Eva's own, the person's and learned skills (re-scanned when they change:
    cheap, mtimes only), plus the imported library (skills/library), which is
    read once from its INDEX.json -- it is large and does not change."""

    def __init__(self):
        self._lock = threading.Lock()
        self._skills = {}
        self._stamp = None
        self._library = None          # name -> Skill, loaded once

    def _signature(self):
        sig = []
        for root, _origin in _roots():
            if not os.path.isdir(root):
                continue
            for base, dirs, names in os.walk(root):
                if os.path.abspath(base) == os.path.abspath(APP_SKILLS):
                    dirs[:] = [d for d in dirs if d != "library"]
                for n in names:
                    if n.endswith(".md"):
                        try:
                            sig.append((os.path.join(base, n), os.path.getmtime(os.path.join(base, n))))
                        except OSError:
                            pass
        return tuple(sorted(sig))

    def _load_library(self):
        index = os.path.join(APP_LIBRARY, "INDEX.json")
        found = {}
        try:
            with open(index, encoding="utf-8") as f:
                entries = json.load(f).get("skills", [])
        except (OSError, ValueError):
            return found
        rank = {s: i for i, s in enumerate(LIBRARY_SOURCES)}
        entries.sort(key=lambda e: (rank.get(e.get("source"), 99), e.get("path", "")))
        for e in entries:
            path = os.path.join(APP_LIBRARY, *e["path"].split("/"))
            if not os.path.isfile(path):
                continue
            try:
                meta, _ = _split_front_matter(_read(path, 8000))
            except OSError:
                continue
            if not meta.get("description") or not _for_windows(meta):
                continue
            name = _slug(e.get("name") or meta.get("name"))
            if name in found:
                name = _slug(f"{e['source']}-{name}")
            if name in found:
                continue
            found[name] = Skill(name, meta["description"][:600], path, os.path.dirname(path), "library",
                                source=e.get("source", ""), category=e.get("category", ""),
                                persona=bool(e.get("persona")))
        return found

    def refresh(self, force=False):
        stamp = self._signature()
        with self._lock:
            if self._library is None or force:
                self._library = self._load_library()
            if not force and stamp == self._stamp:
                return
            found = {}
            for root, origin in _roots():
                if not os.path.isdir(root):
                    continue
                for base, dirs, names in os.walk(root):
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("references", "templates",
                                                                                        "scripts", "assets")]
                    if os.path.abspath(base) == os.path.abspath(APP_SKILLS):
                        dirs[:] = [d for d in dirs if d != "library"]
                    for n in names:
                        if not n.endswith(".md") or n.lower() == "readme.md":
                            continue
                        path = os.path.join(base, n)
                        if n != "SKILL.md" and os.path.exists(os.path.join(base, "SKILL.md")):
                            continue          # a support file next to a SKILL.md
                        try:
                            meta, _ = _split_front_matter(_read(path, 8000))
                        except OSError:
                            continue
                        desc = meta.get("description", "")
                        if not desc or not _for_windows(meta):
                            continue
                        name = _slug(meta.get("name") or (os.path.basename(base) if n == "SKILL.md" else n[:-3]))
                        sk_origin = "learned" if os.path.abspath(path).startswith(os.path.abspath(learned_root())) else origin
                        if name in found and found[name].origin == "app":
                            continue          # Eva's own skill is not shadowed silently
                        found[name] = Skill(name, desc[:600], path, base if n == "SKILL.md" else os.path.dirname(path),
                                            sk_origin)
            # The library fills in every name not already taken.
            merged = dict(self._library)
            for name, s in found.items():
                shadowed = merged.pop(name, None)
                if shadowed is not None:           # keep the library one, under its source's name
                    alias = _slug(f"{shadowed.source}-{name}")
                    if alias not in merged and alias not in found:
                        shadowed.name = alias
                        merged[alias] = shadowed
                merged[name] = s
            self._skills, self._stamp = merged, stamp
            own = sorted(n for n, s in merged.items() if s.origin != "library")
            log.info(f"Skills: {len(merged)} available -- {len(own)} of Eva's and yours "
                     f"({', '.join(own) or 'none'}), {len(merged) - len(own)} in the library.")

    def all(self):
        self.refresh()
        with self._lock:
            return dict(self._skills)

    def get(self, name):
        return self.all().get(_slug(name))

    def catalog(self) -> str:
        skills = self.all()
        if not skills:
            return ""
        own = [s for s in skills.values() if s.origin != "library"]
        lib = [s for s in skills.values() if s.origin == "library"]
        lines, used = [], 0
        for s in sorted(own, key=lambda s: (s.origin != "learned", s.origin != "app", s.name)):
            d = s.description if len(s.description) <= DESC_LIMIT else s.description[:DESC_LIMIT - 1] + "…"
            line = f"- {s.name}: {d}"
            if used + len(line) > CATALOG_BUDGET:
                lines.append(f"- ...and {len(own) - len(lines)} more of these (find_skill searches them)")
                break
            lines.append(line)
            used += len(line)
        text = ("SKILLS. Procedures for particular kinds of task. When a request matches one, call "
                "load_skill(name) first and follow it; a skill is guidance on how, never permission.\n"
                + "\n".join(lines))
        if lib:
            cats = {}
            for s in lib:
                key = "specialist personas" if s.persona else (s.category or s.source)
                cats[key] = cats.get(key, 0) + 1
            top = ", ".join(f"{k} {v}" for k, v in sorted(cats.items(), key=lambda kv: -kv[1])[:16])
            sources = ", ".join(SOURCE_TITLES.get(x, x) for x in LIBRARY_SOURCES
                                if any(s.source == x for s in lib))
            text += (f"\nLIBRARY: {len(lib)} more skills from {sources} ({top}...). For anything specialised "
                     "-- documents (Word, Excel, PowerPoint, PDF), finance, research, writing, design, coding, "
                     "marketing, an expert persona -- call find_skill with a few words first.")
        return text

    def _weights(self, skills):
        """Rarer words say more about a skill than common ones (IDF)."""
        key = len(skills)
        if getattr(self, "_idf_key", None) == key:
            return self._idf
        import math
        df = {}
        for s in skills.values():
            for t in _tokens(s.name.replace("-", " ")) | _tokens(s.description) | _tokens(s.category):
                df[t] = df.get(t, 0) + 1
        n = max(1, len(skills))
        self._idf = {t: math.log(1 + n / c) for t, c in df.items()}
        self._idf_key = key
        return self._idf

    def search(self, query: str, limit: int = 8, category: str = ""):
        words = (query or "").lower()
        q = _tokens(words + " " + " ".join(v for k, v in _SYNONYMS.items() if re.search(rf"\b{k}\b", words)))
        if not q:
            return []
        want_cat = _slug(category) if category else ""
        scored = []
        everything = self.all()
        idf = self._weights(everything)
        for s in everything.values():
            if want_cat and want_cat not in (_slug(s.category), _slug(s.source)) and not (
                    want_cat.startswith("persona") and s.persona):
                continue
            name_t, desc_t, cat_t = _tokens(s.name.replace("-", " ")), _tokens(s.description), _tokens(s.category)
            score = 0.0
            for t in q:
                hit = 3 * (t in name_t) + 2 * (t in cat_t) + (t in desc_t)
                if not hit and len(t) >= 4:          # part of a longer word: humanize ~ human
                    hit = 0.5 * any(w.startswith(t) for w in name_t | desc_t)
                score += idf.get(t, 1.0) * hit
            if score:
                if s.origin != "library":
                    score *= 1.15                  # Eva's own and the person's first, all else equal
                scored.append((score, s))
        scored.sort(key=lambda x: (-x[0], x[1].name))
        return [s for _, s in scored[:max(1, min(int(limit or 8), 20))]]


LIBRARY = Library()

# How skills written for other agents map onto Eva's tools.
ADAPT_NOTE = ("This skill was written for another agent ({source}). Follow its method with YOUR tools: "
              "shell/terminal/bash commands -> run_command (one program, no pipes; anything not read-only asks "
              "the person); read/view a file -> read_file; write/edit/patch a file -> write_file; web search -> "
              "a connected search tool or read_web_page; a browser -> browser_open / browser_act / "
              "browser_extract; screenshots or vision -> look_at_screen; memory -> remember / recall; cron or "
              "schedules -> schedule_task; another skill it names -> load_skill or find_skill. A program it "
              "needs that is not installed: say so and offer to install it (run_command asks first). Steps "
              "that need what you do not have (subagents, git worktrees, a different app's own commands): skip "
              "them or tell the person. Your own rules still apply: short spoken replies, and the framework "
              "decides what needs approval.")
PERSONA_NOTE = ("This is a specialist persona. Use its expertise, methods and checklists for this task, but stay "
                "Eva: same voice (short, plain spoken replies), same rules, same tools.")


# ---- the tools ----------------------------------------------------------------------
def find_skill(query: str, limit: int = 8, category: str = ""):
    hits = LIBRARY.search(query, limit, category)
    if not hits:
        return {"ok": True, "skills": [], "note": "no skill matches; carry on without one"}
    return {"ok": True, "skills": [
        {"name": s.name, "description": s.description[:240],
         "from": {"app": "Eva", "learned": "Eva (learned)", "user": "your skills"}.get(
             s.origin, SOURCE_TITLES.get(s.source, s.source)),
         **({"category": s.category} if s.category else {}), **({"persona": True} if s.persona else {})}
        for s in hits]}


def load_skill(name: str):
    s = LIBRARY.get(name)
    if s is None:
        close = [x.name for x in LIBRARY.search(str(name).replace("-", " "), 6)]
        return {"ok": False, "error": f"no skill called {name!r}", "did_you_mean": close}
    body = s.body()
    out = {"ok": True, "name": s.name, "origin": s.origin, "instructions": body[:BODY_LIMIT], "files": s.files()}
    if s.origin == "library":
        out["adapt"] = ADAPT_NOTE.format(source=SOURCE_TITLES.get(s.source, s.source))
        if s.persona:
            out["persona"] = PERSONA_NOTE
    if len(body) > BODY_LIMIT:
        out["note"] = "the skill is longer than shown; its full text is in the file"
    log.info(f"Skill loaded: {s.name} ({s.origin}{', ' + s.source if s.origin == 'library' else ''})")
    return out


def read_skill_file(name: str, path: str):
    s = LIBRARY.get(name)
    if s is None:
        return {"ok": False, "error": f"no skill called {name!r}"}
    if os.path.basename(s.path) != "SKILL.md":
        return {"ok": False, "error": "this skill has no support files"}
    root = os.path.realpath(s.root)
    target = os.path.realpath(os.path.join(root, str(path or "")))
    if not target.startswith(root + os.sep):
        return {"ok": False, "error": "that path is outside the skill's folder"}
    if os.path.splitext(target)[1].lower() not in TEXT_EXT:
        return {"ok": False, "error": "only text files can be read this way"}
    if not os.path.isfile(target):
        return {"ok": False, "error": f"no file {path!r} in skill {s.name}", "files": s.files()}
    text = _read(target)
    return {"ok": True, "name": s.name, "path": path, "text": text[:FILE_LIMIT],
            **({"truncated": True} if len(text) > FILE_LIMIT else {})}


def unsafe_reason(text: str):
    import system_tools
    if system_tools.looks_like_secret(text):
        return "it contains something that looks like a credential"
    for pattern in _UNSAFE:
        m = pattern.search(text)
        if m:
            return f"it contains wording that would undermine the person's control ({m.group(0)!r})"
    return None


def save_skill(name: str, description: str, instructions: str, *, tainted: bool = False,
               pending: bool = False):
    """Creates or improves one of Eva's learned skills. A replaced version is
    kept as SKILL.md.bak, so a bad edit can be undone. pending=True (anything
    Eva decided to learn on her own) writes it to the review queue instead:
    it is used only after the person approves it in the Activity window."""
    name = str(name or "").strip().lower()
    if not NAME.match(name) or len(name) > 64:
        return {"ok": False, "error": "name must be 1-64 lowercase letters, digits and single hyphens"}
    if tainted:
        return {"ok": False, "error": "not saved: this task read third-party text (a web page or a service), "
                                      "and a page must never be able to write Eva's procedures"}
    description = " ".join(str(description or "").split())
    instructions = str(instructions or "").strip()
    if not (20 <= len(description) <= 600):
        return {"ok": False, "error": "description must say in 20-600 characters what it is for and when to use it"}
    if not (40 <= len(instructions) <= 12000):
        return {"ok": False, "error": "instructions must be 40-12000 characters"}
    why = unsafe_reason(description + "\n" + instructions)
    if why:
        log.warning(f"Refused to save skill {name!r}: {why}")
        return {"ok": False, "error": f"not saved: {why}"}
    existing = LIBRARY.get(name)
    if existing is not None and existing.origin != "learned":
        return {"ok": False, "error": f"{name!r} is a {existing.origin} skill and is not Eva's to change; "
                                      "save the improvement under a new name"}
    folder = os.path.join(pending_root() if pending else learned_root(), name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "SKILL.md")
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    desc_yaml = description.replace("\n", " ")
    content = (f"---\nname: {name}\ndescription: {desc_yaml}\nmetadata:\n  author: Eva (learned)\n"
               f"  updated: {time.strftime('%Y-%m-%d %H:%M')}\n---\n\n{instructions}\n")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)
    if pending:
        log.info(f"Skill {name!r} written for the person to review ({'a revision' if existing else 'new'}).")
        _notify_pending(name)
        return {"ok": True, "name": name, "pending_review": True, "updated": existing is not None, "path": path,
                "note": "saved for the person to review; it is used once they approve it in Activity"}
    LIBRARY.refresh(force=True)
    log.info(f"Skill {'updated' if existing else 'created'}: {name}")
    return {"ok": True, "name": name, "updated": existing is not None, "path": path}


# ---- the review queue ----------------------------------------------------------------
on_pending = None            # main.py: (name) -> tell the person a skill is waiting


def _notify_pending(name):
    try:
        if on_pending:
            on_pending(name)
    except Exception:
        log.exception("Pending-skill notice failed")


def _skill_dirs(root):
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "SKILL.md")))


def _describe(root, name, status):
    path = os.path.join(root, name, "SKILL.md")
    meta, body = _split_front_matter(_read(path))
    return {"name": name, "description": meta.get("description", ""), "status": status, "path": path,
            "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))),
            "replaces": status == "waiting for review" and os.path.isfile(os.path.join(learned_root(), name, "SKILL.md")),
            "body": body}


def review_list():
    """Everything Eva learned: waiting for review first, then in use."""
    out = [_describe(pending_root(), n, "waiting for review") for n in _skill_dirs(pending_root())]
    out += [_describe(learned_root(), n, "in use") for n in _skill_dirs(learned_root())]
    return out


def pending_count() -> int:
    return len(_skill_dirs(pending_root()))


def approve_skill(name):
    """Moves a pending skill into use (the old version, if any, kept as .bak)."""
    name = _slug(name)
    src = os.path.join(pending_root(), name)
    if not os.path.isfile(os.path.join(src, "SKILL.md")):
        return {"ok": False, "error": f"no skill {name!r} is waiting for review"}
    dst = os.path.join(learned_root(), name)
    os.makedirs(dst, exist_ok=True)
    live = os.path.join(dst, "SKILL.md")
    if os.path.exists(live):
        shutil.copy2(live, live + ".bak")
    os.replace(os.path.join(src, "SKILL.md"), live)
    shutil.rmtree(src, ignore_errors=True)
    LIBRARY.refresh(force=True)
    log.info(f"Skill approved by the person: {name}")
    return {"ok": True, "name": name}


def remove_skill(name, status="waiting for review"):
    """Takes a learned or pending skill away. Not erased: moved to
    ~/.eva/skills/.removed/<time>-<name>, so a mistaken click can be undone."""
    name = _slug(name)
    root = pending_root() if status == "waiting for review" else learned_root()
    src = os.path.join(root, name)
    if not os.path.isdir(src):
        return {"ok": False, "error": f"no such skill: {name}"}
    os.makedirs(removed_root(), exist_ok=True)
    dst = os.path.join(removed_root(), f"{time.strftime('%Y%m%d-%H%M%S')}-{name}")
    shutil.move(src, dst)
    LIBRARY.refresh(force=True)
    log.info(f"Skill removed by the person: {name} ({status}) -> {dst}")
    return {"ok": True, "name": name, "kept_at": dst}


def move_learned_to_review_once():
    """One time, when this version first runs: skills Eva learned on her own
    before the review queue existed go to the queue, because some were
    learned from confused sessions. One click in Activity puts them back."""
    marker = os.path.join(user_root(), ".review-migrated")
    if os.path.exists(marker):
        return 0
    moved = 0
    for name in _skill_dirs(learned_root()):
        path = os.path.join(learned_root(), name, "SKILL.md")
        try:
            if "author: Eva (learned)" not in _read(path, 2000):
                continue                      # written by the person: leave it alone
        except OSError:
            continue
        dst = os.path.join(pending_root(), name)
        if os.path.exists(dst):
            continue
        os.makedirs(pending_root(), exist_ok=True)
        shutil.move(os.path.join(learned_root(), name), dst)
        moved += 1
    os.makedirs(user_root(), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M"))
    if moved:
        LIBRARY.refresh(force=True)
        log.info(f"{moved} skill(s) Eva learned on her own were moved to the review queue (one time).")
    return moved
