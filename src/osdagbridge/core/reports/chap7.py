import math

from osdagbridge.core.reports.report_utils import _fig_embed
from osdagbridge.core.utils.common import (
    KEY_MP_CB_BOTTOM_CHORD,
    KEY_MP_CB_BOTTOM_CHORD_SECTION_DESIG,
    KEY_MP_CB_BRACING_SECTION_DESIGNATION,
    KEY_MP_CB_NO_OF_CROSS_BRACINGS,
    KEY_MP_CB_SPACING,
    KEY_MP_CB_TOP_CHORD,
    KEY_MP_CB_TOP_CHORD_SECTION_DESIG,
    KEY_MP_GIRDER_DEPTH,
    KEY_MP_STIFFENER_BEARING_OUTSTAND,
    KEY_MP_STIFFENER_BEARING_THICKNESS,
    KEY_MP_STIFFENER_INTERMEDIATE,
    KEY_MP_STIFFENER_INTERMEDIATE_OUTSTAND,
    KEY_MP_STIFFENER_INTERMEDIATE_SPACING,
    KEY_MP_STIFFENER_INTERMEDIATE_THICKNESS,
    KEY_MP_STIFFENER_NO_BEARING_STIFFENERS,
    KEY_SPAN,
    KEY_TS_GIRDER_SPACING,
    KEY_TS_NO_OF_GIRDERS,
    get_angle_section_properties,
)


def _is_blank(value):
    return value in ("", None, "N.A.", "NA", "None", "---")


def _first_value(input_dict, base_key):
    for key in [base_key] + sorted(k for k in input_dict if k.startswith(base_key + ".")):
        value = input_dict.get(key)
        if not _is_blank(value):
            return value
    return None


def _num(value, default=0.0):
    try:
        text = str(value).strip()
        return default if _is_blank(text) else float(text)
    except Exception:
        return default


def _yes(value):
    return str(value).strip().lower() in {"yes", "true", "1"}


def _angle_area_m2(input_dict, base_key):
    designation = _first_value(input_dict, base_key)
    if not designation:
        return 0.0
    try:
        return _num(get_angle_section_properties(str(designation)).get("Area")) / 10000.0
    except Exception:
        return 0.0


def _ch7_quantity_fallbacks(input_dict):
    span = _num(input_dict.get(KEY_SPAN))
    n_girders = int(_num(input_dict.get(KEY_TS_NO_OF_GIRDERS)))
    girder_spacing = _num(input_dict.get(KEY_TS_GIRDER_SPACING))
    if span <= 0 or n_girders <= 0:
        return {}

    values = {}
    n_panels = int(_num(_first_value(input_dict, KEY_MP_CB_NO_OF_CROSS_BRACINGS)))
    cb_spacing = _num(_first_value(input_dict, KEY_MP_CB_SPACING))
    if cb_spacing <= 0 and n_panels > 0:
        cb_spacing = span / (n_panels + 1)
    if n_panels <= 0 and cb_spacing > 0:
        n_panels = max(1, round(span / cb_spacing) - 1)

    diag_area = _angle_area_m2(input_dict, KEY_MP_CB_BRACING_SECTION_DESIGNATION)
    top_area = _angle_area_m2(input_dict, KEY_MP_CB_TOP_CHORD_SECTION_DESIG) or diag_area
    bot_area = _angle_area_m2(input_dict, KEY_MP_CB_BOTTOM_CHORD_SECTION_DESIG) or top_area
    diag_len = math.hypot(cb_spacing, girder_spacing) if cb_spacing > 0 and girder_spacing > 0 else 0.0

    def _steel(prefix, area, length, qty):
        if area <= 0 or length <= 0 or qty <= 0:
            return
        vol_single = area * length
        vol_total = vol_single * qty
        wt_single = vol_single * 7.85
        wt_total = vol_total * 7.85
        values[f"{prefix}_vol_formula"] = f"${area:.5f}\\text{{ m}}^2 \\times {length:.2f}\\text{{ m}} = {vol_single:.5f}\\text{{ m}}^3$"
        values[f"{prefix}_qty"] = str(qty)
        values[f"{prefix}_vol_total"] = f"{vol_total:.2f}"
        values[f"{prefix}_wt_single"] = f"{wt_single:.4f}"
        values[f"{prefix}_wt_total"] = f"{wt_total:.2f}"

    if n_panels > 0:
        if _yes(_first_value(input_dict, KEY_MP_CB_TOP_CHORD) or "Yes"):
            _steel("bracing_top", top_area, girder_spacing, (n_girders - 1) * n_panels)
        if _yes(_first_value(input_dict, KEY_MP_CB_BOTTOM_CHORD) or "Yes"):
            _steel("bracing_bot", bot_area, girder_spacing, (n_girders - 1) * n_panels)
        _steel("bracing_diag", diag_area, diag_len, (n_girders - 1) * n_panels * 2)

    depth = _num(_first_value(input_dict, KEY_MP_GIRDER_DEPTH))
    stiff_vol = 0.0
    stiff_qty = 0
    bearing_t = _num(_first_value(input_dict, KEY_MP_STIFFENER_BEARING_THICKNESS))
    bearing_w = _num(_first_value(input_dict, KEY_MP_STIFFENER_BEARING_OUTSTAND))
    bearing_count = int(_num(_first_value(input_dict, KEY_MP_STIFFENER_NO_BEARING_STIFFENERS)))
    if depth > 0 and bearing_t > 0 and bearing_w > 0 and bearing_count > 0:
        qty = n_girders * 2 * bearing_count
        stiff_qty += qty
        stiff_vol += qty * depth * bearing_t * bearing_w / 1e9

    if _yes(_first_value(input_dict, KEY_MP_STIFFENER_INTERMEDIATE) or "No"):
        int_t = _num(_first_value(input_dict, KEY_MP_STIFFENER_INTERMEDIATE_THICKNESS))
        int_w = _num(_first_value(input_dict, KEY_MP_STIFFENER_INTERMEDIATE_OUTSTAND))
        int_spacing = _num(_first_value(input_dict, KEY_MP_STIFFENER_INTERMEDIATE_SPACING))
        if depth > 0 and int_t > 0 and int_w > 0 and int_spacing > 0:
            qty = n_girders * max(0, int((span * 1000.0) / int_spacing) - 1) * 2
            stiff_qty += qty
            stiff_vol += qty * depth * int_t * int_w / 1e9

    if stiff_qty > 0 and stiff_vol > 0:
        stiff_wt = stiff_vol * 7.85
        values["stiffeners_vol_formula"] = f"${stiff_vol / stiff_qty:.6f}\\text{{ m}}^3 \\times {stiff_qty} = {stiff_vol:.5f}\\text{{ m}}^3$"
        values["stiffeners_qty"] = str(stiff_qty)
        values["stiffeners_vol_total"] = f"{stiff_vol:.2f}"
        values["stiffeners_wt_single"] = f"{stiff_wt / stiff_qty:.4f}"
        values["stiffeners_wt_total"] = f"{stiff_wt:.2f}"
    return values


def ch7_quantities(input_dict, chart_paths=None):
    input_dict = {**_ch7_quantity_fallbacks(input_dict),
                  **{k: v for k, v in input_dict.items() if not _is_blank(v)}}
    chart_paths = chart_paths or {}
    chart_figures = ""
    if chart_paths:
        chart_figures = (
            "\n\\vspace{1em}\n"
            + _fig_embed(chart_paths.get("steel"),
                         "Structural Steel Tonnage Summary",
                         width=r"0.82\textwidth", numbered=True)
            + "\n\\vspace{0.5em}\n"
            + _fig_embed(chart_paths.get("concrete_rebar"),
                         "Concrete Volume and Reinforcement Steel Summary",
                         width=r"0.82\textwidth", numbered=True)
        )
    return r"""
\chapter{Bill of Materials}
\label{ch:material-takeoff}

\noindent\textbf{Table 7.1  Bill of Materials (Steel, Concrete, and Reinforcement Quantities)}

\begingroup
\setlength{\tabcolsep}{3.5pt}
\begin{longtable}{|C{1.0cm}|L{3.8cm}|C{2.6cm}|C{1.8cm}|C{1.8cm}|C{1.8cm}|C{1.8cm}|}
\hline
\textbf{S.N.} & \textbf{Item Description} & \textbf{Volume} & \textbf{Quantity} & \textbf{Total Volume} & \textbf{Weight (M)} & \textbf{Total Weight (T)} \\
\hline
1 & Structural Steel (IS 2062) for Girders & """ + str(input_dict.get("steel_girders_vol_formula", "N.A.")) + r""" & """ + str(input_dict.get("steel_girders_qty", "N.A.")) + r""" & """ + str(input_dict.get("steel_girders_vol_total", "N.A.")) + r""" & """ + str(input_dict.get("steel_girders_wt_single", "N.A.")) + r""" & """ + str(input_dict.get("steel_girders_wt_total", "N.A.")) + r""" \\
\hline
2(a) & Cross Bracing - Top Chord & """ + str(input_dict.get("bracing_top_vol_formula", "N.A.")) + r""" & """ + str(input_dict.get("bracing_top_qty", "N.A.")) + r""" & """ + str(input_dict.get("bracing_top_vol_total", "N.A.")) + r""" & """ + str(input_dict.get("bracing_top_wt_single", "N.A.")) + r""" & """ + str(input_dict.get("bracing_top_wt_total", "N.A.")) + r""" \\
\hline
2(b) & Cross Bracing - Bottom Chord & """ + str(input_dict.get("bracing_bot_vol_formula", "N.A.")) + r""" & """ + str(input_dict.get("bracing_bot_qty", "N.A.")) + r""" & """ + str(input_dict.get("bracing_bot_vol_total", "N.A.")) + r""" & """ + str(input_dict.get("bracing_bot_wt_single", "N.A.")) + r""" & """ + str(input_dict.get("bracing_bot_wt_total", "N.A.")) + r""" \\
\hline
2(c) & Cross Bracing - Diagonal Chord & """ + str(input_dict.get("bracing_diag_vol_formula", "N.A.")) + r""" & """ + str(input_dict.get("bracing_diag_qty", "N.A.")) + r""" & """ + str(input_dict.get("bracing_diag_vol_total", "N.A.")) + r""" & """ + str(input_dict.get("bracing_diag_wt_single", "N.A.")) + r""" & """ + str(input_dict.get("bracing_diag_wt_total", "N.A.")) + r""" \\
\hline
3 & Stiffeners & """ + str(input_dict.get("stiffeners_vol_formula", "N.A.")) + r""" & """ + str(input_dict.get("stiffeners_qty", "N.A.")) + r""" & """ + str(input_dict.get("stiffeners_vol_total", "N.A.")) + r""" & """ + str(input_dict.get("stiffeners_wt_single", "N.A.")) + r""" & """ + str(input_dict.get("stiffeners_wt_total", "N.A.")) + r""" \\
\hline
4 & Connections & """ + str(input_dict.get("connections_vol_formula", "N.A.")) + r""" & """ + str(input_dict.get("connections_qty", "N.A.")) + r""" & """ + str(input_dict.get("connections_vol_total", "N.A.")) + r""" & """ + str(input_dict.get("connections_wt_single", "N.A.")) + r""" & """ + str(input_dict.get("connections_wt_total", "N.A.")) + r""" \\
\hline
5 & Concrete (M40) for Deck Slab & """ + str(input_dict.get("concrete_deck_vol_formula", "N.A.")) + r""" & """ + str(input_dict.get("concrete_deck_qty", "N.A.")) + r""" & """ + str(input_dict.get("concrete_deck_vol_total", "N.A.")) + r""" & """ + str(input_dict.get("concrete_deck_wt_single", "N.A.")) + r""" & """ + str(input_dict.get("concrete_deck_wt_total", "N.A.")) + r""" \\
\hline
6 & Reinforcement Steel (Fe 500) & """ + str(input_dict.get("rebar_deck_vol_formula", "N.A.")) + r""" & """ + str(input_dict.get("rebar_deck_qty", "N.A.")) + r""" & """ + str(input_dict.get("rebar_deck_vol_total", "N.A.")) + r""" & """ + str(input_dict.get("rebar_deck_wt_single", "N.A.")) + r""" & """ + str(input_dict.get("rebar_deck_wt_total", "N.A.")) + r""" \\
\hline
7 & Shear Stud Connectors & """ + str(input_dict.get("shear_studs_vol_formula", "N.A.")) + r""" & """ + str(input_dict.get("shear_studs_qty", "N.A.")) + r""" & """ + str(input_dict.get("shear_studs_vol_total", "N.A.")) + r""" & """ + str(input_dict.get("shear_studs_wt_single", "N.A.")) + r""" & """ + str(input_dict.get("shear_studs_wt_total", "N.A.")) + r""" \\
\hline
8 & Crash Barrier & """ + str(input_dict.get("crash_barrier_vol_formula", "N.A.")) + r""" & """ + str(input_dict.get("crash_barrier_qty", "N.A.")) + r""" & """ + str(input_dict.get("crash_barrier_vol_total", "N.A.")) + r""" & """ + str(input_dict.get("crash_barrier_wt_single", "N.A.")) + r""" & """ + str(input_dict.get("crash_barrier_wt_total", "N.A.")) + r""" \\
\hline
\end{longtable}
""" + chart_figures


