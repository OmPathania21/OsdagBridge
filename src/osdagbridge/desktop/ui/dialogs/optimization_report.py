"""
optimization_report.py
-----------------------
Popup tab summarising an Overall Design Check (threaded weight optimisation):
one row per evaluated design — feasible or not — showing the values that were
tried and the resulting component weights, with the winning design highlighted.

Fed by ``PlateGirderBridge.optimization_records`` / ``.optimization_summary``,
populated by ``optimize_threaded``.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtCore import Qt


_COLUMNS = [
    ("#",          "index"),
    ("Round",      "round"),
    ("Status",     "status"),
    ("Girders n",  "n"),
    ("Spacing (m)", "spacing_m"),
    ("Slab (mm)",  "t_slab_mm"),
    ("Depth D (mm)", "D_mm"),
    ("Flange bf (mm)", "bf_mm"),
    ("Flange tf (mm)", "tf_mm"),
    ("Web tw (mm)", "tw_mm"),
    ("Steel (kN)", "steel_kN"),
    ("Deck (kN)",  "deck_kN"),
    ("Total (kN)", "total_kN"),
]

_PASS_BG = QColor("#E7F6E7")
_FAIL_BG = QColor("#FBE9E9")
_BEST_BG = QColor("#C8E6C9")


class OptimizationReportDialog(QDialog):
    """Modal table of all designs evaluated during the optimisation run."""

    def __init__(self, records: List[dict], summary: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Overall Design Check — Design Iterations")
        self.resize(1050, 640)

        layout = QVBoxLayout(self)

        summary = summary or {}
        evaluated  = summary.get("evaluated", len(records))
        feasible   = summary.get("feasible", sum(1 for r in records if r.get("feasible")))
        infeasible = summary.get("infeasible", evaluated - feasible)
        best_kN    = summary.get("best_total_kN")

        header = QLabel(
            f"Evaluated {evaluated} designs   •   "
            f"{feasible} feasible / {infeasible} infeasible"
            + (f"   •   lightest feasible: {best_kN:.1f} kN" if best_kN is not None else "")
        )
        hf = QFont(); hf.setPointSize(11); hf.setBold(True)
        header.setFont(hf)
        layout.addWidget(header)

        note = QLabel(
            "Every design tried by the search is listed below, including "
            "infeasible ones (failed an IRC 22:2015 check). The winning design "
            "used for the CAD is highlighted."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        layout.addWidget(note)

        self.table = self._build_table(records)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _build_table(self, records: List[dict]) -> QTableWidget:
        table = QTableWidget(len(records), len(_COLUMNS))
        table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(False)

        for row, rec in enumerate(records):
            is_best = rec.get("is_best", False)
            feasible = rec.get("feasible", False)
            bg = _BEST_BG if is_best else (_PASS_BG if feasible else _FAIL_BG)
            for col, (_, key) in enumerate(_COLUMNS):
                val = rec.get(key, "")
                if key == "status" and is_best:
                    val = "BEST ✓"
                item = QTableWidgetItem(str(val))
                if key in ("steel_kN", "deck_kN", "total_kN", "spacing_m"):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(QBrush(bg))
                if is_best:
                    f = item.font(); f.setBold(True); item.setFont(f)
                table.setItem(row, col, item)

        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table
