---
name: tidy-a-folder
description: Use when the person asks to clean up, organise or sort a folder (Downloads, Desktop, Documents) or to put files into folders by type, date or project.
---

# Tidy a folder

1. **Look first.** `list_folder` the folder. Count files by kind: documents (pdf, docx, txt),
   images, videos, audio, archives (zip, rar, 7z), installers (exe, msi), other.
2. **Propose in one sentence** unless the person already said how: "I'll sort 48 files into
   Documents, Images, Installers and Archives folders inside Downloads." Proceed unless they
   object; moving files asks them for approval anyway.
3. **Create the folders** with `create_folder`, inside the folder being tidied.
4. **Move** with `move_file`, one file per call. Never overwrite: if a name exists in the target,
   leave that file where it is and mention it at the end.
5. **Report**: how many moved where, anything skipped, and that "undo that" puts a file back
   (`undo_last_change`; `list_changes` shows the moves).

Pitfalls
- Skip files in use or half-downloaded (`.crdownload`, `.part`, `.tmp`).
- Never tidy system folders, program folders or a project with source code without being asked.
- The person approves moves; if they decline one, do not retry it a different way.
