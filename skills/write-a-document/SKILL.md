---
name: write-a-document
description: Use when the person asks Eva to write something to keep (notes, a summary, a letter, a list, a report, an essay) or to save findings to a file, rather than just say it.
---

# Write a document

1. **Decide the file.** Default: `Documents/<short title> - <YYYY-MM-DD>.txt` (or `.md` for notes
   with headings). Use the folder or name the person gives, if any.
2. **Write it directly** with `write_file(path, content, mode="create")`. Do not type it into
   Notepad: writing the file is faster and is checked by reading it back.
   - A file of that name exists: pick a new name with " (2)", never overwrite unless asked.
3. **Show it.** `launch_app("notepad", path)` (or the person's preferred editor if remembered)
   so they can see it, then say in one sentence where it is.
4. **Changes later.** "Add a line" means `mode="append"`; "rewrite it" means `mode="overwrite"`
   (asks first, and can be undone with `undo_last_change`).

Pitfalls
- Never put passwords, keys or card numbers in a document.
- Long content is fine in the file; keep what you SAY to one or two sentences.
