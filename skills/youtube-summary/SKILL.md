---
name: youtube-summary
description: Use when the person wants to know what a YouTube video says, a summary of it, or an answer from it, given a link, the video on screen, or a title to find.
---

# YouTube summary

1. **Get the address.** From the person's message; or, if it is playing on screen, from the
   browser (`get_foreground_app` gives the window; `read_window_text` or `browser_extract` the
   address bar); or search for the title and take the official upload.
2. **Get the words.** `youtube_transcript(url)`. If it says captions are off or blocked, say so
   and offer to watch for the main points with `look_at_screen` while it plays, instead.
3. **Summarise.** Spoken: one sentence on what the video is, then the three to five points
   that matter, then any conclusion or recommendation it gives. If the person asked a specific
   question, answer only that, from the transcript.
4. **Longer notes on request.** Write them with the write-a-document skill: title, link, the
   points as short paragraphs, and timestamps if the person wants them.

Pitfalls
- A transcript of auto-generated captions has no punctuation and mishears names; don't quote
  it word for word and check proper nouns against the video title.
- Long videos are truncated in the result; say "from the first part" if `truncated` is true.
