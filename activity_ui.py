"""Eva -- "Activity and memory" window.

Taken from the pattern in CopilotKit/OpenDots' Dot computer panel (activity
records, human control) and Open Dots' visible approvals/audit: the person can
SEE what Eva has been doing, stop it, and see/delete what she remembers.

* Activity: the last actions from the audit log, refreshed every 1.5 s while
  the window is open. Red = failed or denied. A Stop button does exactly what
  saying "stop" does.
* Memory: every stored fact (untrusted web-derived notes marked), delete one or
  forget everything.

Read-only on the audit log; memory edits go through Memory's own methods.
Qt only; the data shaping lives in plain functions that run_checks tests.
"""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)


def activity_rows(events, limit=100):
    """Newest first: (time, tool, outcome, detail, bad)."""
    out = []
    for e in reversed(events[-limit:]):
        ts = e.get("ts")
        when = time.strftime("%H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else ""
        if e.get("denied"):
            outcome, bad = "denied", True
        elif e.get("ok") is False:
            outcome, bad = "failed", True
        elif e.get("verified") is False:
            outcome, bad = "done (unverified)", False
        else:
            outcome, bad = "done", False
        args = e.get("args") or {}
        detail = e.get("error") or ", ".join(f"{k}={str(v)[:40]}" for k, v in list(args.items())[:3])
        if e.get("decision") and e["decision"] not in ("allowed", "auto", None):
            detail = f"[{e['decision']}] {detail}"
        out.append((when, str(e.get("tool", "?")), outcome, detail[:160], bad))
    return out


def memory_rows(facts):
    return [(f["key"], f["value"][:160], f["category"] + ("" if f["trusted"] else " (from the web, untrusted)"),
             f["key"]) for f in facts]


class ActivityWindow(QDialog):
    def __init__(self, audit_log, memory, on_stop, parent=None):
        super().__init__(parent)
        self._audit, self._memory, self._on_stop = audit_log, memory, on_stop
        self.setWindowTitle("Eva - activity and memory")
        self.resize(780, 480)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        act = QWidget()
        al = QVBoxLayout(act)
        self._note = QLabel("")
        al.addWidget(self._note)
        self._act = QTableWidget(0, 4)
        self._act.setHorizontalHeaderLabels(["Time", "Action", "Result", "Detail"])
        self._act.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._act.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        al.addWidget(self._act)
        stop = QPushButton("Stop what Eva is doing")
        stop.clicked.connect(self._stop)
        al.addWidget(stop)
        tabs.addTab(act, "Activity")

        mem = QWidget()
        ml = QVBoxLayout(mem)
        self._mem = QTableWidget(0, 3)
        self._mem.setHorizontalHeaderLabels(["Remembered", "Value", "Kind"])
        self._mem.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._mem.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._mem.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        ml.addWidget(self._mem)
        row = QHBoxLayout()
        b1 = QPushButton("Forget selected")
        b1.clicked.connect(self._forget_selected)
        b2 = QPushButton("Forget everything")
        b2.clicked.connect(self._forget_all)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        ml.addLayout(row)
        tabs.addTab(mem, "Memory")

        # Skills Eva wrote herself: nothing she learns on her own is used
        # until it is approved here.
        sk = QWidget()
        sl = QVBoxLayout(sk)
        self._sk_note = QLabel("")
        self._sk_note.setWordWrap(True)
        sl.addWidget(self._sk_note)
        self._sk = QTableWidget(0, 4)
        self._sk.setHorizontalHeaderLabels(["Skill", "Status", "When", "What it is for"])
        for col in (0, 1, 2):
            self._sk.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._sk.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._sk.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._sk.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._sk.itemSelectionChanged.connect(self._show_skill)
        sl.addWidget(self._sk, 2)
        self._sk_text = QPlainTextEdit()
        self._sk_text.setReadOnly(True)
        self._sk_text.setPlaceholderText("Select a skill to read exactly what Eva wrote.")
        sl.addWidget(self._sk_text, 3)
        srow = QHBoxLayout()
        for label, fn in (("Approve selected", self._approve), ("Approve all waiting", self._approve_all),
                          ("Remove selected", self._remove)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            srow.addWidget(b)
        srow.addStretch(1)
        sl.addLayout(srow)
        self._skills_tab = tabs.addTab(sk, "Skills")
        self._tabs = tabs
        self._sk_rows = []

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    def showEvent(self, e):
        self._timer.start(1500)
        self.refresh()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def refresh(self):
        try:
            if self._audit is None:
                self._note.setText("The audit log is not enabled.")
            else:
                rows = activity_rows(self._audit.recent(100))
                self._note.setText(f"{len(rows)} recent actions, newest first." if rows
                                   else "Nothing done yet this session.")
                self._act.setRowCount(len(rows))
                for r, (when, tool, outcome, detail, bad) in enumerate(rows):
                    for c, text in enumerate((when, tool, outcome, detail)):
                        item = QTableWidgetItem(text)
                        if bad:
                            item.setForeground(Qt.GlobalColor.red)
                        self._act.setItem(r, c, item)
            self._refresh_memory()
            self._refresh_skills()
        except Exception as e:                      # a view must never break Eva
            self._note.setText(f"Could not refresh: {e}")

    def _refresh_memory(self):
        if self._memory is None:
            self._mem.setRowCount(0)
            return
        rows = memory_rows(self._memory.list_facts())
        self._mem.setRowCount(len(rows))
        for r, (key, value, kind, _k) in enumerate(rows):
            for c, text in enumerate((key, value, kind)):
                self._mem.setItem(r, c, QTableWidgetItem(text))

    def _stop(self):
        try:
            self._on_stop()
        finally:
            self._note.setText("Stop requested.")

    def _forget_selected(self):
        if self._memory is None:
            return
        for idx in sorted({i.row() for i in self._mem.selectedIndexes()}, reverse=True):
            item = self._mem.item(idx, 0)
            if item:
                self._memory.forget(item.text())
        self._refresh_memory()

    def _forget_all(self):
        if self._memory is None:
            return
        if QMessageBox.question(self, "Forget everything",
                                "Erase every fact, episode and learned procedure Eva has stored?"
                                ) == QMessageBox.StandardButton.Yes:
            self._memory.forget_all()
            self._refresh_memory()

    # ---- skills ------------------------------------------------------------------------
    def show_skills(self):
        self._tabs.setCurrentIndex(self._skills_tab)

    def _refresh_skills(self):
        from core import skills
        rows = skill_rows(skills.review_list())
        if [r[:3] for r in rows] == [r[:3] for r in self._sk_rows]:
            return                                    # unchanged: keep the selection
        self._sk_rows = rows
        waiting = sum(1 for r in rows if r[1] == "waiting for review")
        self._sk_note.setText(
            (f"{waiting} skill(s) Eva wrote herself are waiting for you. She does not use them until you "
             "approve them. " if waiting else "Nothing is waiting for review. ")
            + "Removed skills are kept in .eva\\skills\\.removed, so a mistake can be undone.")
        self._sk.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row[:4]):
                item = QTableWidgetItem(text)
                if row[1] == "waiting for review":
                    item.setForeground(Qt.GlobalColor.darkYellow)
                self._sk.setItem(r, c, item)

    def _selected_skills(self):
        return [self._sk_rows[i] for i in sorted({i.row() for i in self._sk.selectedIndexes()})
                if i < len(self._sk_rows)]

    def _show_skill(self):
        sel = self._selected_skills()
        self._sk_text.setPlainText(sel[0][4] if sel else "")

    def _approve(self):
        from core import skills
        for row in self._selected_skills():
            if row[1] == "waiting for review":
                skills.approve_skill(row[0])
        self._sk_rows = []
        self._refresh_skills()
        self._skills_changed()

    def _skills_changed(self):
        cb = getattr(self, "on_skills_changed", None)
        if callable(cb):
            cb()

    def _approve_all(self):
        from core import skills
        for row in list(self._sk_rows):
            if row[1] == "waiting for review":
                skills.approve_skill(row[0])
        self._sk_rows = []
        self._refresh_skills()
        self._skills_changed()

    def _remove(self):
        from core import skills
        sel = self._selected_skills()
        if not sel:
            return
        names = ", ".join(r[0] for r in sel)
        if QMessageBox.question(self, "Remove skills", f"Remove {names}? Eva will stop using them "
                                "(they are kept in .removed, not erased).") != QMessageBox.StandardButton.Yes:
            return
        for row in sel:
            skills.remove_skill(row[0], row[1])
        self._sk_rows = []
        self._sk_text.clear()
        self._refresh_skills()
        self._skills_changed()


def skill_rows(items):
    """(name, status, when, description, full text), waiting-for-review first."""
    return [(i["name"], i["status"], i["updated"],
             (("REVISION of the skill in use: " if i.get("replaces") else "") + i["description"])[:200],
             i["body"]) for i in items]
