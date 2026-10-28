# Eva's skills

Each folder here is a skill in the open **Agent Skills** format (agentskills.io): a `SKILL.md`
with front matter (`name`, `description`) and instructions, plus optional `references/`,
`templates/` and `scripts/`. Eva sees only each skill's name and description, and reads the
body with `load_skill` when a request matches.

**The library** (`library/`) holds 479 skills imported by `tools/import_skills.py` from Hermes
Agent (MIT), superpowers (MIT), agency-agents (MIT) and Anthropic (Apache-2.0 only). Re-run the
script against fresh clones to update it. `library/NOTICE.md` lists sources, commits, licences and
every exclusion: Anthropic's proprietary docx/pdf/pptx/xlsx and unlicensed doc-coauthoring;
skills that do not run on Windows; a jailbreak kit and a person-tracking tool. Eva lists her own
skills in her prompt and searches the library with `find_skill`; a library skill is loaded with a
note mapping its tools to hers.

**Adding skills.** Put a skill folder in `%USERPROFILE%\.eva\skills\` (tray → Skills folder).
Skills written for other agents work unchanged where their steps fit Eva's tools:

- anthropics/skills — format reference. Note: its docx/pdf/pptx/xlsx skills are proprietary and
  may not be redistributed; the Apache-2.0 ones (e.g. skill-creator, internal-comms) may.
- NousResearch/hermes-agent `skills/` (MIT) — e.g. productivity and research skills.
- obra/superpowers (MIT) — process skills (mainly for software work).
- msitarzewski/agency-agents (MIT) — specialist personas; a single `.md` with `name` and
  `description` front matter is read as a skill.

**Learned skills** that Eva writes from experience go to `%USERPROFILE%\.eva\skills\learned\`.
Each save keeps the previous version as `SKILL.md.bak`. Delete a folder to make her forget it.

Skills are guidance on how to do things. They never grant permission: what needs your approval
is decided by the framework, which does not read skills.
