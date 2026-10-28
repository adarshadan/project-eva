---
name: daily-briefing
description: Use when the person asks for a briefing or morning summary, or asks to set one up on a schedule ("every weekday at 9, brief me").
---

# Daily briefing

## Setting it up
1. Ask nothing you can default. Default time 09:00 on weekdays, unless the person said otherwise.
2. `schedule_task(instruction="Give me my daily briefing", at_time="09:00", repeat="weekdays")`.
3. Confirm in one sentence: when it runs, and that they can say "cancel my briefing" to stop it.
4. If they name topics or sources (cricket, a news site, a blog), remember them with
   `remember(key="briefing topics", value=..., category="preference")`.

## Giving the briefing (also when it runs on schedule)
Keep it under a minute when spoken, in this order:
1. **Day and time**, from `get_current_time`.
2. **What is scheduled today**: `list_scheduled`, only today's items.
3. **News**: their remembered topics or feeds (`recall("briefing topics")`), via `read_feed` for
   feeds or the web-research skill for topics. Two or three headlines, one line each.
4. **One useful thing about the computer**, only if notable (`system_info`: battery low, disk
   nearly full).
5. End by offering one follow-up ("Want the full story on any of those?").

Pitfalls
- If a source fails, skip it silently in the briefing and mention it once at the end.
- Don't read out links or long lists; this is spoken.
