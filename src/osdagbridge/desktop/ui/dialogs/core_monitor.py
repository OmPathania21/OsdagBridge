"""
core_monitor.py — live "which core is working on which design" popup.

A small, non-modal dialog showing one row per CPU core used by the parallel
overall-design search. As workers stream start/done events from the isolated
runner, each row updates to show the core's current design (girder section +
running design number), its status, and how long the last design took.

The dialog is purely a view: ``on_core_event`` feeds it the JSON dicts the
runner emits (``{"type": "core", "ev": "start"|"done", ...}``); it owns no
process / thread state of its own.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)

_COLS = ["Core", "PID", "Design #", "Girder section", "Status", "Last time"]


class CoreMonitorDialog(QDialog):
    """Live per-core activity view for the parallel overall-design search."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Overall Design — Core Activity")
        self.setModal(False)
        self.resize(640, 320)

        layout = QVBoxLayout(self)

        self._summary = QLabel("Waiting for cores to pick up designs …")
        self._summary.setTextFormat(Qt.RichText)
        layout.addWidget(self._summary)

        self._table = QTableWidget(0, len(_COLS), self)
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        for c in (0, 1, 2, 4, 5):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        layout.addWidget(self._table)

        # core slot -> table row
        self._row_of: dict[int, int] = {}
        self._completed = 0
        self._active = 0

    # -- internal helpers ----------------------------------------------------

    def _row(self, core: int) -> int:
        """Return (creating if needed) the table row for a core slot."""
        if core not in self._row_of:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._row_of[core] = row
            for col in range(len(_COLS)):
                self._table.setItem(row, col, QTableWidgetItem(""))
            self._table.item(row, 0).setText(f"Core {core + 1}")
        return self._row_of[core]

    def _set(self, row: int, col: int, text: str, color: QColor | None = None) -> None:
        item = self._table.item(row, col)
        item.setText(text)
        if color is not None:
            item.setForeground(color)

    def _refresh_summary(self) -> None:
        self._summary.setText(
            f"<b>{self._active}</b> core(s) working &nbsp;|&nbsp; "
            f"<b>{self._completed}</b> design(s) completed"
        )

    # -- event sink ----------------------------------------------------------

    def on_core_event(self, msg: dict) -> None:
        """Consume one runner 'core' message and update the matching row."""
        if msg.get("core") is None:
            return
        row = self._row(int(msg["core"]))
        ev = msg.get("ev")

        if ev == "start":
            section = (
                f"D{int(msg['D'])} · bf{int(msg['bf'])} · "
                f"tf{int(msg['tf'])} · tw{int(msg['tw'])} · {int(msg['n'])} girders"
                if msg.get("D") is not None else "—"
            )
            self._set(row, 1, str(msg.get("pid", "")))
            self._set(row, 2, f"#{msg.get('design_no', '')}")
            self._set(row, 3, section)
            self._set(row, 4, "⏳ designing…", QColor("#b8860b"))
            self._set(row, 5, "")
            self._active += 1

        elif ev == "done":
            feasible = msg.get("feasible")
            if feasible:
                w = msg.get("weight")
                self._set(row, 4, f"✓ PASS  {w:.0f} kN" if w is not None else "✓ PASS",
                          QColor("#1a7f37"))
            else:
                self._set(row, 4, "✗ infeasible", QColor("#cf222e"))
            secs = msg.get("secs")
            self._set(row, 5, f"{secs:.1f} s" if secs is not None else "")
            self._completed += 1
            self._active = max(0, self._active - 1)

        self._refresh_summary()

    def mark_finished(self, note: str = "Run finished.") -> None:
        self._active = 0
        self._summary.setText(
            f"{note} &nbsp;|&nbsp; <b>{self._completed}</b> design(s) completed."
        )
