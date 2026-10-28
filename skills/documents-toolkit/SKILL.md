---
name: documents-toolkit
description: Use for everyday document work - summarise or answer from a PDF or Word file, merge or split PDFs, save Word as PDF, or pull tables out of a PDF into Excel.
---

# Documents toolkit

- **Find the file** with `find_files` (kind "document"); ask only if several match.
- **Summarise / answer questions:** `read_file` (PDF, DOCX, text). For a long file read it in parts
  with `offset`. A summary longer than a sentence or two goes in the reply (opened in Notepad).
  For several long files, `delegate` the reading to a helper.
- **Merge PDFs:** `merge_pdfs([paths in order])`. **Some pages:** `split_pdf(path, "1-3, 7")`.
- **Word to PDF:** `word_to_pdf(path)` (needs Microsoft Word).
- **Tables to Excel:** `pdf_tables_to_csv(path)`; say it is a best guess from spacing.
- Every result is a NEW file beside the original; say its name. Originals are never changed.

Pitfalls
- A scanned PDF has no text: say so, and offer `look_at_screen` on the open document instead.
