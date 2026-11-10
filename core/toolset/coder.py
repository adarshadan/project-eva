"""The coding helper: programming jobs handed to OpenCode on this PC (coder.py)."""
import coder
from core.effects import Effect as E, Surface as S
from core.registry import INT, STR, register
from core.toolset.desktop import _short

register("code_task", "Hand a PROGRAMMING job to the coding helper (OpenCode, with its free models) so you stay "
         "free: write a script or small program, build a web page or tool, fix or explain code in a folder, "
         "analyse a data file with code. It works in the background without a window, in its own project folder "
         "(Documents\\Eva\\code\\<project>) or in an existing folder the person named. Returns at once; its "
         "report arrives later as a [system] note. Not for on-screen work, and not for a few lines you can write "
         "yourself with write_file.",
         {"task": STR("The whole job: what to build, in which language if it matters, and how the person will "
                      "use it. Include every detail they gave -- the helper cannot ask them."),
          "project": STR("A short name for a new project folder, e.g. photo-renamer."),
          "folder": STR("An existing folder to work in instead (full path)."),
          "model": STR("Leave empty.")}, ["task"],
         surface=S.FILES, effect=E.MODIFY, fn=coder.start, verb="send", embodied=False, deadline=20,
         describe=lambda a: f"Handing {_short(a.get('project') or a.get('task'), 30)} to the coding helper")
register("code_status", "The coding helper's jobs: running, finished or failed, with each report and folder.",
         surface=S.FILES, effect=E.OBSERVE, fn=coder.status, verb="look", embodied=False, deadline=10)
register("cancel_code_task", "Stop a coding job (by number) or, without a number, all of them.",
         {"job": INT("The job's number; leave out to stop all.")},
         surface=S.FILES, effect=E.OBSERVE, fn=coder.cancel, verb="close", embodied=False, deadline=15)
