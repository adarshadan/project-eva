"""Eva -- recognising a control whose press cannot be taken back. Pure logic, no I/O.

eva.log 2026-10-17 11:21: from a misheard "update install" she opened Windows Update and
invoked the "Restart now" button (element id SystemSettings_MusUpdate_UpdateActionButton3_
SplitButton). Nothing asked: the permission gate judged clicks only for MONEY (finance_guard),
and "Restart now" is not money. The PC restarted with the person's work open.

The person's rule is: act freely, ask for destructive actions and money. A click's effect
cannot be read off the tool name, so -- as finance_guard does for money -- this judges what is
actually being pressed: the control's own label, read from the control (never from what the
model calls it). It leans toward asking: a needless prompt costs a click, a missed one can cost
the person's unsaved work, their files, or an installed program.
"""
import re

# Pressing these restarts or powers off the PC, signs the person out, deletes, uninstalls,
# wipes or resets something, or throws away unsaved work.
_DESTRUCTIVE = re.compile(
    r"\b("
    r"restart(\s+now)?|reboot|shut\s*down|power\s*off|turn\s+off\s+(the\s+)?(pc|computer)|"
    r"update\s+and\s+(restart|shut\s*down)|install\s+and\s+(restart|shut\s*down)|"
    r"sign\s*out|log\s*off|log\s*out|switch\s+user|"
    r"uninstall|remove(\s+(app|program|account|device|user|profile|all))?|"
    r"delete(\s+\w+)?|permanently|erase|wipe|format(\s+disk|\s+drive)?|"
    r"reset(\s+(this\s+pc|pc|app|settings|all|to\s+default))?|factory\s+reset|restore\s+defaults|"
    r"empty\s+(the\s+)?(recycle\s+bin|trash|bin)|clear\s+(all|data|history|browsing\s+data|cache)|"
    r"discard(\s+changes)?|don'?t\s+save|do\s+not\s+save|overwrite|replace\s+(the\s+)?files?|"
    r"end\s+task|force\s+(quit|close|stop)|kill(\s+process)?|"
    r"close\s+(my\s+)?account|deactivate"
    r")\b", re.I)

# Labels that only LOOK destructive: declining, postponing, or reading about it.
_HARMLESS = re.compile(
    r"^\s*(cancel|not\s+now|later|no(\s+thanks)?|back|close(?!\s+(my\s+)?account)|dismiss|skip|keep|undo)\b|"
    r"\b(restart\s+later|schedule\s+(the\s+)?restart|pause\s+updates|learn\s+more|"
    r"restart\s+options|advanced\s+options|what'?s\s+this|more\s+info)\b|"
    r"^\s*(don'?t|do\s+not)\s+(restart|delete|remove|uninstall)\b", re.I)


def control_reason(label: str):
    """Why pressing a control labelled `label` cannot be taken back, or None."""
    text = " ".join(str(label or "").split())
    if not text:
        return None
    # "(Button)" / "(SplitButton)" suffixes from describe_element do not change the meaning.
    bare = re.sub(r"\s*\((split\s*)?button|\s*\(menu\s*item\)|\s*\(list\s*item\)|\s*\(hyperlink\)", "", text,
                  flags=re.I)
    if _HARMLESS.search(bare):
        return None
    # "Empty" as the whole label is the command; "4th letter, empty" (a Wordle tile, live test 2026-10-28)
    # or "Empty folder" text is a description, not a press that wipes anything.
    if re.fullmatch(r"empty(\s+now)?", bare.strip(), re.I):
        return "deletes, wipes or resets something"
    m = _DESTRUCTIVE.search(bare)
    if not m:
        return None
    word = m.group(0).lower()
    if re.match(r"restart|reboot|shut|power|turn off|update and|install and", word):
        why = "restarts or shuts down the PC, closing everything that is open"
    elif re.match(r"sign|log|switch user", word):
        why = "signs you out, closing everything that is open"
    elif re.match(r"uninstall|remove", word):
        why = "removes something"
    elif re.match(r"discard|don|do not|overwrite|replace", word):
        why = "throws away or overwrites work"
    elif re.match(r"end task|force|kill", word):
        why = "force-closes a program, losing its unsaved work"
    else:
        why = "deletes, wipes or resets something"
    return f"“{text[:50]}” {why}"
