---
name: blocked-page-recovery
description: Use when a web page will not load or read properly - a "verify you are human" check, a login wall, an access-denied page, an empty page, or a site that keeps failing.
---

# Blocked page recovery

Human checks (captchas, "press and hold", image grids) are never attempted. They exist to tell
a person from a program; working around them misrepresents the person's presence.

1. **Try the light route first.** If the browser failed, try `read_web_page(url)`; if
   `read_web_page` failed, `browser_open(url)` once. Different routes are blocked differently.
2. **Human check in the browser.** Do nothing more on that page. The framework brings the
   browser forward and tells the person; carry on when it resumes you.
3. **Login wall.** Ask the person whether to wait while they sign in, in Eva's browser or
   their own (`navigate_browser` drives their open browser, which has their logins).
4. **Still blocked.** Find the same information elsewhere: another reputable source via the
   web-research skill, the site's official app if installed (`launch_app`), or a cached copy
   from a search result. Tell the person which source you used instead.
5. **Stop retrying** the same blocked address after two attempts; say plainly what is blocked.
