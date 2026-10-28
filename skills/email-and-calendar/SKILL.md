---
name: email-and-calendar
description: Use when the person wants to write or reply to an email, or to put a meeting, appointment or reminder in their calendar.
---

# Email and calendar (drafts the person sends)

**Email.** Write the full email yourself, in their voice: short, clear, with a greeting and a sign-off
using their name if you know it. Then `draft_email(to, subject, body)`: it opens in their mail app,
and THEY press Send. Say: "The draft is open -- check it and press Send." If they selected text to
reply to, `get_selected_text` gives it.

**Calendar.** Work out the date and time from their words and `get_current_time` ("next Tuesday at
3" -> a real date). `add_calendar_event(title, "YYYY-MM-DD HH:MM", minutes, location, notes)`: their
calendar app shows it and they save it. For a reminder that Eva herself should give, use
`schedule_reminder` instead.

Pitfalls
- Never claim an email was sent or an event saved: you opened a draft.
- If an address is missing, leave "to" empty rather than guessing.
