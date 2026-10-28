"""Eva -- the Settings window (tray > Settings, or "open your settings").

Built from settings_store.SETTINGS, so a new setting needs one line there and
nothing here. Save writes ~/.eva/settings.json and applies the values at once;
the ones that need a restart are named in the window and in the reply.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

import settings_store as store
from chat_ui import FONT_FAMILY


class SettingsWindow(QDialog):
    def __init__(self, parent=None, on_saved=None):
        super().__init__(parent)
        self.setWindowTitle("Eva -- Settings")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumSize(520, 560)
        # A LIGHT window must say so to every child, not only to itself: style-sheet colours are
        # not inherited, so in Windows dark mode the labels, values and the Save button were
        # white on white (2026-10-17, live test). The palette IS inherited by child widgets.
        pal = self.palette()
        for role, colour in ((QPalette.ColorRole.Window, "#FFFFFF"), (QPalette.ColorRole.Base, "#FFFFFF"),
                             (QPalette.ColorRole.AlternateBase, "#F2F2F7"), (QPalette.ColorRole.Button, "#F2F2F7"),
                             (QPalette.ColorRole.WindowText, "#1C1C1E"), (QPalette.ColorRole.Text, "#1C1C1E"),
                             (QPalette.ColorRole.ButtonText, "#1C1C1E"), (QPalette.ColorRole.PlaceholderText, "#8E8E93"),
                             (QPalette.ColorRole.ToolTipBase, "#FFFFFF"), (QPalette.ColorRole.ToolTipText, "#1C1C1E")):
            pal.setColor(role, QColor(colour))
        self.setPalette(pal)
        self.setStyleSheet(f"""
            QDialog, QWidget#page {{ background: #FFFFFF; color: #1C1C1E; font-family: '{FONT_FAMILY}';
                                     font-size: 10pt; }}
            QGroupBox {{ font-weight: 600; border: 1px solid #D1D1D6; border-radius: 8px; margin-top: 14px;
                         padding: 10px 10px 6px 10px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
            QLabel, QCheckBox, QGroupBox {{ color: #1C1C1E; }}
            QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {{ color: #1C1C1E; background: #FFFFFF;
                border: 1px solid #C7C7CC; border-radius: 5px; padding: 2px 6px; }}
            QComboBox QAbstractItemView {{ color: #1C1C1E; background: #FFFFFF;
                selection-background-color: #0A84FF; selection-color: #FFFFFF; }}
            QPushButton {{ color: #1C1C1E; background: #F2F2F7; border: 1px solid #C7C7CC; border-radius: 6px;
                padding: 5px 14px; }}
            QPushButton:default {{ color: #FFFFFF; background: #0A84FF; border-color: #0A84FF; }}
            QLabel#help {{ color: #545458; font-size: 8.5pt; }}
            QLabel#note {{ color: #0A6CDB; }}
        """)
        self._on_saved = on_saved
        self._widgets = {}
        values = store.current()

        page = QWidget()
        page.setObjectName("page")
        col = QVBoxLayout(page)
        groups = {}
        for spec in store.SETTINGS:
            g = spec["group"]
            if g not in groups:
                box = QGroupBox(g)
                groups[g] = QFormLayout(box)
                groups[g].setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                col.addWidget(box)
            w = self._make(spec, values.get(spec["key"]))
            self._widgets[spec["key"]] = w
            label = spec["label"] + ("" if spec["live"] else "  *")
            if spec["help"]:
                cell = QWidget()
                v = QVBoxLayout(cell)
                v.setContentsMargins(0, 0, 0, 0)
                v.setSpacing(1)
                v.addWidget(w)
                h = QLabel(spec["help"])
                h.setObjectName("help")
                h.setWordWrap(True)
                v.addWidget(h)
                groups[g].addRow(label, cell)
            else:
                groups[g].addRow(label, w)
        star = QLabel("* takes effect the next time Eva starts")
        star.setObjectName("help")
        col.addWidget(star)
        col.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)

        self.note = QLabel("")
        self.note.setObjectName("note")
        self.note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        defaults = QPushButton("Restore defaults")
        defaults.clicked.connect(self.restore_defaults)
        bottom = QHBoxLayout()
        bottom.addWidget(defaults)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll, 1)
        outer.addWidget(self.note)
        outer.addLayout(bottom)

    # ---- widgets ------------------------------------------------------------------------
    def _make(self, spec, value):
        kind = spec["kind"]
        if kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(value))
        elif kind == "choice":
            w = QComboBox()
            w.addItems(spec["choices"])
            if value in spec["choices"]:
                w.setCurrentText(value)
        elif kind == "int":
            w = QSpinBox()
            w.setRange(int(spec["min"]), int(spec["max"]))
            w.setSingleStep(int(spec["step"] or 1))
            w.setValue(int(value or 0))
        elif kind == "float":
            w = QDoubleSpinBox()
            w.setDecimals(3)
            w.setRange(float(spec["min"]), float(spec["max"]))
            w.setSingleStep(float(spec["step"] or 0.1))
            w.setValue(float(value or 0))
        else:
            w = QLineEdit(str(value or ""))
            w.setPlaceholderText("e.g. <cmd>+<shift>+e   (empty = off)")
        return w

    def _read(self, key, w):
        spec = store.BY_KEY[key]
        if spec["kind"] == "bool":
            return w.isChecked()
        if spec["kind"] == "choice":
            return w.currentText()
        if spec["kind"] in ("int", "float"):
            return w.value()
        return w.text()

    def _write(self, key, w, value):
        spec = store.BY_KEY[key]
        if spec["kind"] == "bool":
            w.setChecked(bool(value))
        elif spec["kind"] == "choice":
            w.setCurrentText(str(value))
        elif spec["kind"] in ("int", "float"):
            w.setValue(value)
        else:
            w.setText(str(value or ""))

    # ---- actions --------------------------------------------------------------------------
    def save(self):
        res = store.save({k: self._read(k, w) for k, w in self._widgets.items()})
        self._report(res)

    def restore_defaults(self):
        for k, w in self._widgets.items():
            if k in store.DEFAULTS:
                self._write(k, w, store.DEFAULTS[k])
        self.save()

    def _report(self, res):
        if res["errors"]:
            self.note.setText("Not saved: " + "; ".join(res["errors"]))
            return
        if not res["changed"]:
            self.note.setText("Nothing changed.")
        elif res["restart"]:
            self.note.setText(f"Saved. {', '.join(res['restart'])}: after a restart; the rest now.")
        else:
            self.note.setText("Saved -- in effect now.")
        if self._on_saved:
            self._on_saved(res)


_OPEN = {"w": None}


def open_settings(parent=None, on_saved=None):
    """Show the window (one at a time). GUI thread only."""
    w = _OPEN["w"]
    if w is None or not w.isVisible():
        w = SettingsWindow(parent, on_saved=on_saved)
        _OPEN["w"] = w
    w.show()
    w.raise_()
    w.activateWindow()
    return w
