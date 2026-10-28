"""Builds Eva's skill library (skills/library/) from the reference repositories.

    python tools/import_skills.py --hermes <path> --superpowers <path>
                                  --agency <path> --anthropic <path>

Each argument is a local clone (git clone --depth 1 ...). Re-running replaces
the library, so it doubles as the update procedure.

What is imported, and what is not:
  * Only skills whose licence allows redistribution. Anthropic's docx, pdf,
    pptx and xlsx skills are proprietary ("governed by your agreement with
    Anthropic") and doc-coauthoring carries no licence: all five are left out.
    Hermes, superpowers and agency-agents are MIT; the Anthropic skills taken
    are Apache-2.0. Each source's licence is copied beside its skills, and
    NOTICE.md records every source, licence and exclusion.
  * Skills that declare platforms without Windows (Apple Notes, iMessage,
    Linux-only tooling) are left out: Eva runs on Windows.
  * Two skills are left out on content grounds (CONTENT_EXCLUDED): a jailbreak
    kit for bypassing AI safety filters, and a tool for tracking a person's
    accounts across sites.
  * agency-agents personas are single Markdown files with name/description
    front matter; they are imported as single-file skills.
INDEX.json lists every imported skill (name, source, category, path, licence),
which Eva uses for the library catalogue and search.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

SOURCES = {
    "hermes": {"title": "NousResearch/hermes-agent", "url": "https://github.com/NousResearch/hermes-agent",
               "dirs": ["skills", "optional-skills"], "licence": "MIT", "licence_file": "LICENSE"},
    "superpowers": {"title": "obra/superpowers", "url": "https://github.com/obra/superpowers",
                    "dirs": ["skills"], "licence": "MIT", "licence_file": "LICENSE"},
    "agency-agents": {"title": "msitarzewski/agency-agents", "url": "https://github.com/msitarzewski/agency-agents",
                      "dirs": ["."], "licence": "MIT", "licence_file": "LICENSE"},
    "anthropic": {"title": "anthropics/skills", "url": "https://github.com/anthropics/skills",
                  "dirs": ["skills"], "licence": "Apache-2.0 (per skill LICENSE.txt)", "licence_file": None},
}
ANTHROPIC_EXCLUDED = {
    "docx": "proprietary licence (governed by an agreement with Anthropic); not redistributable",
    "pdf": "proprietary licence (governed by an agreement with Anthropic); not redistributable",
    "pptx": "proprietary licence (governed by an agreement with Anthropic); not redistributable",
    "xlsx": "proprietary licence (governed by an agreement with Anthropic); not redistributable",
    "doc-coauthoring": "no licence file, so no permission to redistribute",
}
# Left out on content grounds, whatever the licence says.
CONTENT_EXCLUDED = {
    "security/godmode": "a jailbreak kit for bypassing AI models' safety filters",
    "security/sherlock": "finds a person's accounts across 400+ sites by username, i.e. tracks individuals",
}
AGENCY_SKIP_DIRS = {".git", ".github", "scripts", "integrations", "examples", "docs"}
MAX_FILE_BYTES = 2 * 1024 * 1024
SKIP_NAMES = {".DS_Store", "Thumbs.db"}


def front_matter(path):
    text = open(path, encoding="utf-8", errors="replace").read(12000)
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    meta = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            meta[m.group(1).lower()] = m.group(2).strip().strip("'\"")
    return meta


def platforms_allow_windows(meta):
    raw = meta.get("platforms")
    if not raw:
        return True
    return "windows" in raw.lower()


def commit_of(repo):
    try:
        return subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def copy_tree(src, dst):
    for base, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for n in names:
            if n in SKIP_NAMES or n.endswith(".pyc"):
                continue
            s = os.path.join(base, n)
            if os.path.getsize(s) > MAX_FILE_BYTES:
                continue
            d = os.path.join(dst, os.path.relpath(s, src))
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)


def import_folder_skills(source, repo, out, index, excluded):
    for top in SOURCES[source]["dirs"]:
        root = os.path.join(repo, top)
        for base, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "index-cache")
            if "SKILL.md" not in names:
                continue
            dirs[:] = []                                    # support folders belong to this skill
            meta = front_matter(os.path.join(base, "SKILL.md"))
            folder = os.path.basename(base)
            name = meta.get("name") or folder
            rel = os.path.relpath(base, root).replace("\\", "/")
            category = rel.split("/")[0] if "/" in rel else source
            if source == "anthropic" and folder in ANTHROPIC_EXCLUDED:
                excluded.append((source, folder, ANTHROPIC_EXCLUDED[folder]))
                continue
            if source == "anthropic" and not os.path.exists(os.path.join(base, "LICENSE.txt")):
                excluded.append((source, folder, "no licence file"))
                continue
            if rel in CONTENT_EXCLUDED:
                excluded.append((source, rel, CONTENT_EXCLUDED[rel]))
                continue
            if not meta.get("description"):
                excluded.append((source, rel, "no description in its front matter"))
                continue
            if not platforms_allow_windows(meta):
                excluded.append((source, rel, f"not for Windows (platforms: {meta.get('platforms')})"))
                continue
            dest_rel = f"{source}/{top.strip('./') or 'skills'}/{rel}" if source == "hermes" else f"{source}/{rel}"
            copy_tree(base, os.path.join(out, dest_rel))
            index.append({"name": name, "source": source, "category": category, "path": dest_rel + "/SKILL.md",
                          "licence": meta.get("license") or SOURCES[source]["licence"]})


def import_agency(repo, out, index, excluded):
    for base, dirs, names in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in AGENCY_SKIP_DIRS and not d.startswith("."))
        rel_dir = os.path.relpath(base, repo).replace("\\", "/")
        if rel_dir == ".":
            continue                                        # README, CONTRIBUTING...
        for n in sorted(names):
            if not n.endswith(".md") or n.upper().startswith(("README", "CONTRIBUTING")):
                continue
            path = os.path.join(base, n)
            meta = front_matter(path)
            if not meta.get("description") or not meta.get("name"):
                continue
            dest_rel = f"agency-agents/{rel_dir}/{n}"
            os.makedirs(os.path.dirname(os.path.join(out, dest_rel)), exist_ok=True)
            shutil.copy2(path, os.path.join(out, dest_rel))
            index.append({"name": meta["name"], "source": "agency-agents", "category": rel_dir.split("/")[0],
                          "path": dest_rel, "licence": "MIT", "persona": True})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for key in ("hermes", "superpowers", "agency", "anthropic"):
        ap.add_argument(f"--{key}", required=True)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                  "skills", "library"))
    a = ap.parse_args()
    repos = {"hermes": a.hermes, "superpowers": a.superpowers, "agency-agents": a.agency, "anthropic": a.anthropic}
    out = os.path.abspath(a.out)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    index, excluded = [], []
    for source, repo in repos.items():
        if not os.path.isdir(repo):
            sys.exit(f"not a folder: {repo}")
        if source == "agency-agents":
            import_agency(repo, out, index, excluded)
        else:
            import_folder_skills(source, repo, out, index, excluded)
        lic = SOURCES[source]["licence_file"]
        if lic and os.path.exists(os.path.join(repo, lic)):
            os.makedirs(os.path.join(out, source), exist_ok=True)
            shutil.copy2(os.path.join(repo, lic), os.path.join(out, source, "LICENSE"))
    with open(os.path.join(out, "INDEX.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": datetime.date.today().isoformat(), "skills": index}, f, indent=1)
    counts = {s: sum(1 for i in index if i["source"] == s) for s in repos}
    lines = ["# Skill library: sources and licences", "",
             f"Generated {datetime.date.today().isoformat()} by tools/import_skills.py. "
             "These skills were written by their authors for other agents; Eva adapts their steps to her tools.", ""]
    for s, repo in repos.items():
        meta = SOURCES[s]
        lines += [f"## {meta['title']}", f"- Source: {meta['url']} (commit {commit_of(repo)})",
                  f"- Licence: {meta['licence']}" + (f" -- full text in `{s}/LICENSE`" if meta["licence_file"] else
                                                     " -- full text in each skill's LICENSE.txt"),
                  f"- Skills imported: {counts[s]}", ""]
    lines += ["## Not imported", ""] + [f"- {s}: `{what}` -- {why}" for s, what, why in excluded]
    with open(os.path.join(out, "NOTICE.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Imported {len(index)} skills ({counts}); excluded {len(excluded)}. Library: {out}")


if __name__ == "__main__":
    main()
