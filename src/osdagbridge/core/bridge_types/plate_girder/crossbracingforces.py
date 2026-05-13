"""
CrossBracingForces
------------------
Resolves grillage analysis forces into design axial forces for the
diagonals and chords of intermediate cross bracing between adjacent
plate girders.

Supported brace types
---------------------
  X-type  Two full diagonals crossing the full width × height panel.
  K-type  Two diagonals from the TOP FLANGE of each girder converging
          at the CENTRE of the bottom chord (chevron / inverted-V).
          The top chord is optional.

Step-wise process
-----------------
  Step 1  Identify brace configuration.
          Brace type (X / K), top/bottom chord presence, and panel
          spacing are read from PlateGirderBridge.additional_inputs.

  Step 2  Compute brace geometry.
          Girder depth D, spacing s, and span L come from
          PlateGirderBridge (section_props, sizing_result,
          grillage_geometry).  Diagonal angle and length follow.

  Step 3  Read girder forces from analysis results.
          Three force components are extracted at each cross-bracing
          station from PlateGirderAnalysisResults:
            Vz  transverse (horizontal) shear in the girder element
            Mx  torsional moment in the girder element
            Fx  axial force in the girder element
          Both ends of each element are read; the end with the larger
          absolute magnitude governs.  Both ends can carry forces in the
          same direction under distributed loading, so the abs-max ensures
          the governing value is always captured.
          These girder forces pass directly into the cross-bracing members
          as axial forces (Step 4 below).
          Internal station-to-node mapping locates the right element;
          once analysis_results.py exposes a direct station-query API,
          that internal lookup can be replaced transparently.

  Step 4  Resolve member forces.
          Each girder force component transfers into a specific
          cross-bracing member as an axial force:
            Vz (transverse shear) → compressive diagonal
            Mx (torsion)          → both diagonal and chord
            Fx (axial)            → chord directly
          See "Force resolution" below for the exact expressions.

  Step 5  Tabulate and envelope for design.
          Forces are assembled into a full DataFrame and enveloped
          per girder pair; get_design_forces_dict() packages the
          result for the cross-bracing design module.

Geometry reference
------------------
X-type  (elevation of the transverse plane between two girders)

    G_i ──── top chord ──── G_(i+1)     y = h  (top flange level)
     │                           │
      \\                         /
       \\          D2           /
        \\                     /
    D1   ──────── X ────────       (diagonals cross at mid-panel)
        /                     \\
       /          D3            \\
      /                         \\
     │                           │
    G_i ─── bottom chord ──── G_(i+1)   y = 0  (bottom flange level)
         |<──────── s ─────────>|

    alpha_X = atan(h / s)
    L_d_X   = sqrt(s² + h²)

K-type (inverted-V / chevron)

    G_i ──── top chord ──── G_(i+1)     y = h  (optional top chord)
     │                           │
      \\                         /
       \\                       /
        \\                     /
         \\                   /          alpha_K = atan(h / (s/2))
          \\                 /           L_d_K   = sqrt((s/2)² + h²)
           ──── ─── *─── ────            centre node  (z = s/2)
     │         s/2   s/2          │
    G_i ─── bottom chord ──── G_(i+1)   y = 0

Force resolution
----------------
Two girder force components drive the cross-bracing design:

  Fx  axial force in the girder (kN)  — goes directly into the chord.
  Mz  bending moment in the girder (kNm) — converted to an equivalent
      axial force (AF) via the lever arm h (brace clear height):

        F_mz  =  |Mz| / h          (kN)

  The combined axial-force demand per girder side:

        F_af_L  =  |Fx_L|  +  |Mz_L| / h
        F_af_R  =  |Fx_R|  +  |Mz_R| / h

  Both sides transfer into the diagonal, so the total panel demand is:

        F_total  =  F_af_L  +  F_af_R

  Resolving along the diagonal axis (angle α from horizontal):

        F_diag   =  F_total / cos α

  The governing chord force is taken from the worse of the two sides:

        F_chord  =  max(F_af_L, F_af_R)

  where
    Fx_L, Fx_R   (kN)   — axial force in each brace-node girder
    Mz_L, Mz_R   (kNm)  — bending moment in each brace-node girder
    h             (m)    — brace clear height
    α             (rad)  — atan(h / horiz_proj)

  For K-type, horiz_proj = s/2, giving a steeper α and larger diagonal
  forces than X-type for the same loading.

Usage
-----
    pgb = PlateGirderBridge()
    pgb.set_input(input_dict)
    pgb.design()

    results = pgb.get_result_handler()
    cb = CrossBracingForces(bridge=pgb, results=results)

    df   = cb.compute_panel_forces()        # full table
    crit = cb.get_critical_forces()         # envelope per pair
    d    = cb.get_design_forces_dict()      # for design module
    cb.print_critical_forces()
"""

from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from osdagbridge.core.utils.common import (
    DEFAULT_CROSS_BRACING_SPACING,
    KEY_CROSS_BRACING_SPACING,
    KEY_CROSS_BRACING_TYPE,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRACE_X = "X"           # X-type cross bracing
BRACE_K = "K"           # K-type (inverted-V / chevron) cross bracing

# Keys used to look up chord configuration from additional_inputs
_KEY_TOP_CHORD    = "Cross Bracing Top Chord"
_KEY_BOTTOM_CHORD = "Cross Bracing Bottom Chord"


# ===========================================================================
class CrossBracingForces:
    """
    Step-wise force analysis for X-type or K-type cross bracing.

    Parameters
    ----------
    bridge : PlateGirderBridge
        Fully solved bridge (design() already called).
    results : PlateGirderAnalysisResults
        Analysis handler from bridge.get_result_handler().
    brace_type : str or None
        'X' or 'K'.  If None, read from bridge.additional_inputs
        [KEY_CROSS_BRACING_TYPE]; default 'X'.
    top_chord : bool or None
        True if a top chord connects the two girders at the top flange.
        None → read from additional_inputs.  Default True.
    bottom_chord : bool or None
        True if a bottom chord connects the two girders at the bottom
        flange.  None → read from additional_inputs.  Default True.
    cb_spacing : float or None
        Cross-bracing panel spacing (m).  None → read from inputs.
    depth_ratio : float
        Brace clear height = D × depth_ratio.  Default 0.85.
    include_edge_beams : bool
        Include EB1/EB2 edge beams in pair scanning.  Default False.
    """

    def __init__(
        self,
        bridge,
        results=None,
        brace_type:    Optional[str]   = None,
        top_chord:     Optional[bool]  = None,
        bottom_chord:  Optional[bool]  = None,
        cb_spacing:    Optional[float] = None,
        depth_ratio:   float = 0.85,
        include_edge_beams: bool = False,
    ):
        self.bridge = bridge
        self.depth_ratio = depth_ratio
        self.include_edge_beams = include_edge_beams

        # Step 1: brace type, chord presence, panel spacing (from bridge)
        self._identify_configuration(brace_type, top_chord, bottom_chord)

        # Step 2: diagonal geometry from bridge section/sizing data
        self._init_geometry(cb_spacing)

    # =======================================================================
    # STEP 1 — IDENTIFY BRACE CONFIGURATION
    # =======================================================================

    def _identify_configuration(
        self,
        brace_type:   Optional[str],
        top_chord:    Optional[bool],
        bottom_chord: Optional[bool],
    ) -> None:
        """
        Determine brace type (X or K) and which chords are present.

        Priority:  argument > additional_inputs > default.
        """
        ai = getattr(self.bridge, "additional_inputs", {})

        # --- Brace type ---
        if brace_type is not None:
            raw = str(brace_type).strip().upper()
        else:
            raw = str(ai.get(KEY_CROSS_BRACING_TYPE, BRACE_X)).strip().upper()

        if raw not in (BRACE_X, BRACE_K):
            raise ValueError(
                f"Unsupported brace_type '{raw}'. Choose 'X' or 'K'."
            )
        self.brace_type: str = raw

        # --- Top chord ---
        if top_chord is not None:
            self.top_chord = bool(top_chord)
        else:
            val = ai.get(_KEY_TOP_CHORD, "Yes")
            self.top_chord = str(val).strip().lower() not in ("no", "false", "0")

        # --- Bottom chord ---
        if bottom_chord is not None:
            self.bottom_chord = bool(bottom_chord)
        else:
            val = ai.get(_KEY_BOTTOM_CHORD, "Yes")
            self.bottom_chord = str(val).strip().lower() not in ("no", "false", "0")

    # =======================================================================
    # STEP 2 — BRACE GEOMETRY
    # =======================================================================

    def _init_geometry(self, cb_spacing: Optional[float]) -> None:
        """
        Compute brace geometry from the solved bridge.

        Common quantities (both types)
        ───────────────────────────────
          h   clear brace height  = D × depth_ratio  (m)
          s   girder spacing                          (m)
          L   bridge span                             (m)

        X-type specific
        ───────────────
          alpha_X = atan(h / s)          angle of full diagonal from horizontal
          L_d_X   = sqrt(s² + h²)        full diagonal length

        K-type specific
        ───────────────
          alpha_K = atan(h / (s/2))      angle of half-diagonal from horizontal
          L_d_K   = sqrt((s/2)² + h²)    half-diagonal length
        """
        sizing = getattr(self.bridge, "sizing_result", None)
        geom   = getattr(self.bridge, "grillage_geometry", None)

        if sizing is None or geom is None:
            raise RuntimeError(
                "CrossBracingForces requires bridge.design() to have been called first."
            )

        # --- Panel spacing ---
        if cb_spacing is not None:
            self.cb_spacing = float(cb_spacing)
        else:
            ai = getattr(self.bridge, "additional_inputs", {})
            self.cb_spacing = float(
                ai.get(KEY_CROSS_BRACING_SPACING, DEFAULT_CROSS_BRACING_SPACING)
            )

        # --- Girder section dimensions (metres) ---
        sp = self.bridge.section_props
        self.D      = float(sp["D"])
        self.tf_top = float(sp.get("t_f_top", 0.0))
        self.tf_bot = float(sp.get("t_f_bot", self.tf_top))

        # --- Common geometry ---
        self.h = self.D * self.depth_ratio          # brace clear height (m)
        self.s = float(sizing.girder_spacing)        # girder spacing (m)
        self.L = float(geom.L)                       # bridge span (m)

        # --- Type-specific diagonal geometry ---
        if self.brace_type == BRACE_X:
            self.horiz_proj = self.s               # horizontal projection of diagonal
        else:  # K-type: diagonals go to centre of bottom chord
            self.horiz_proj = self.s / 2.0

        self.L_d      = math.sqrt(self.horiz_proj ** 2 + self.h ** 2)
        self.alpha_rad = math.atan2(self.h, self.horiz_proj)
        self.cos_alpha = math.cos(self.alpha_rad)

    # -----------------------------------------------------------------------
    # Private helper — station-to-node mapping
    # Locates the nearest grillage node (and its element) for each
    # cross-bracing x-position along every girder.  This detail is
    # internal to Step 3; when analysis_results.py gains a direct
    # station-query API it can be replaced without changing the interface.
    # -----------------------------------------------------------------------

    def _build_station_map(self) -> tuple[dict, dict]:
        """
        Derive cross-bracing stations directly from transverse members in
        bridge.result_data.  Each unique x-position shared by the two nodes
        of a transverse member is a station; girder assignment is determined
        by matching Z-coordinates to longitudinal member groups.

        Returns
        -------
        station_map : dict
            { station_x (rounded m) ->
                { girder_name ->
                    { node, element, is_i_node, actual_x } } }
        girder_map : dict
            { girder_name -> {"z": float} } — ordered by Z.
        """
        from collections import defaultdict

        rd          = self.bridge.result_data
        nodes_dict  = rd["nodes"]                   # str(tag) → [x, y, z]
        members_dict = rd["members"]                # str(tag) → [n1, n2]
        trans_tags  = rd["transverse_members"]      # list[int]
        long_tags   = rd["longitudinal_members"]    # list[int]
        edge_dist   = rd["edge_dist"]

        # ── Build longitudinal girder groups (same Z) ─────────────────────
        _z_tol = 1e-3
        girder_elems: dict = defaultdict(list)   # z_rounded → [(x_start, eid, n1, n2)]
        for eid in long_tags:
            n1, n2 = members_dict[str(eid)]
            z = round(float(nodes_dict[str(n1)][2]), 3)
            x1 = float(nodes_dict[str(n1)][0])
            girder_elems[z].append((x1, eid, n1, n2))
        for z in girder_elems:
            girder_elems[z].sort(key=lambda t: t[0])

        sorted_z = sorted(girder_elems.keys())
        n_g = len(sorted_z)
        if edge_dist > 0:
            def _gname(i):
                if i == 0:          return "EB1"
                if i == n_g - 1:    return "EB2"
                return f"G{i}"
        else:
            def _gname(i):
                return f"G{i + 1}"
        girder_names = {z: _gname(i) for i, z in enumerate(sorted_z)}
        girder_map   = {girder_names[z]: {"z": z} for z in sorted_z}

        # ── node → adjacent longitudinal element ──────────────────────────
        node_to_long: dict = {}   # node_id(int) → (eid, is_i_node)
        for eid in long_tags:
            n1, n2 = members_dict[str(eid)]
            node_to_long.setdefault(n1, (eid, True))
            node_to_long.setdefault(n2, (eid, False))

        # ── Collect stations from transverse member nodes ─────────────────
        station_nodes: dict = defaultdict(set)   # x_rounded → set[node_id]
        for eid in trans_tags:
            n1, n2 = members_dict[str(eid)]
            x = round(float(nodes_dict[str(n1)][0]), 3)
            station_nodes[x].add(n1)
            station_nodes[x].add(n2)

        # ── Build station_map ─────────────────────────────────────────────
        node_z = {int(k): round(float(v[2]), 3) for k, v in nodes_dict.items()}

        station_map: dict = {}
        for sx in sorted(station_nodes.keys()):
            station_map[sx] = {}
            for nid in station_nodes[sx]:
                z = node_z.get(nid)
                if z is None or z not in girder_names:
                    continue
                g_name = girder_names[z]
                if nid in node_to_long:
                    eid, is_i = node_to_long[nid]
                    station_map[sx][g_name] = {
                        "node":      nid,
                        "element":   eid,
                        "is_i_node": is_i,
                        "actual_x":  float(nodes_dict[str(nid)][0]),
                    }

        return station_map, girder_map

    # =======================================================================
    # STEP 3 — READ GIRDER FORCES FROM ANALYSIS RESULTS
    # =======================================================================

    def _read_component(
        self,
        lc: str,
        eid: int,
        is_i: bool,
        base: str,
    ) -> Optional[float]:
        """
        Read one force/moment component from bridge.result_data (flat dict).

        Parameters
        ----------
        base : str
            Component root name without end suffix: 'Vz', 'Mx', 'Fx', 'Vy', etc.
            The suffix '_i' or '_j' is appended based on is_i.

        Returns
        -------
        float in raw SI units (N or Nm), or None if the component is absent.
        """
        comp = base + ("_i" if is_i else "_j")
        try:
            return float(
                self.bridge.result_data["forces"][str(lc)][str(int(eid))][comp]
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _governing_af(
        self,
        lc: str,
        eid: int,
        is_i_node: bool,
    ) -> Optional[tuple[float, float, float]]:
        """
        Return (Fx_kN, Mz_kNm, F_af_kN) at the bracing station node.

        The station is at one specific node (is_i_node).  Mz and Fx at that
        node are the physically relevant values — the opposite end belongs to
        the adjacent bracing panel and must not be mixed in.

        Returns None if Fx is absent for this element.
        """
        fx = self._read_component(lc, eid, is_i_node, "Vx")   # ospgrillage: axial = Vx
        if fx is None:
            return None

        mz = self._read_component(lc, eid, is_i_node, "Mz") or 0.0
        fx = fx or 0.0
        af = abs(fx) + abs(mz) / self.h

        return fx / 1e3, mz / 1e3, af / 1e3

    def _extract_forces(
        self,
        lc: str,
        info_l: dict,
        info_r: dict,
    ) -> Optional[dict]:
        """
        For each girder side, compute the governing (Fx, Mz, F_af) using the
        station node as the primary read, falling back to the opposite end
        when it gives a larger combined F_af.

        Returns None if Fx is absent for either girder.
        """
        gov_l = self._governing_af(lc, info_l["element"], info_l["is_i_node"])
        gov_r = self._governing_af(lc, info_r["element"], info_r["is_i_node"])

        if gov_l is None or gov_r is None:
            return None

        return {
            "fx_l":   gov_l[0], "mz_l":   gov_l[1], "f_af_l": gov_l[2],
            "fx_r":   gov_r[0], "mz_r":   gov_r[1], "f_af_r": gov_r[2],
        }

    # =======================================================================
    # STEP 4 — RESOLVE MEMBER FORCES
    # =======================================================================

    def _resolve_forces(
        self,
        F_af_left_kN:  float,
        F_af_right_kN: float,
    ) -> dict:
        """
        Convert pre-computed per-side axial-force demands into member forces.

        F_af per side is already the governing combined demand (|Fx| + |Mz|/h)
        at the governing node — computed in _governing_af.

        Both sides transfer through the diagonal:

            F_diag  =  (F_af_L + F_af_R) / cos α

        Governing chord:

            F_chord  =  max(F_af_L, F_af_R)

        Returns
        -------
        dict with keys:
            F_total_kN   combined panel demand (kN)
            F_diag_kN    diagonal design force (kN)
            F_chord_kN   governing chord design force (kN)
        """
        if self.cos_alpha < 1e-9:
            return {"F_total_kN": 0.0, "F_diag_kN": 0.0, "F_chord_kN": 0.0}

        F_total = F_af_left_kN + F_af_right_kN
        F_diag  = F_total / self.cos_alpha
        F_chord = max(F_af_left_kN, F_af_right_kN)

        return {
            "F_total_kN": round(F_total, 4),
            "F_diag_kN":  round(F_diag,  4),
            "F_chord_kN": round(F_chord, 4),
        }

    # =======================================================================
    # STEP 5 — TABULATE AND ENVELOPE FOR DESIGN
    # =======================================================================

    def compute_panel_forces(
        self,
        load_case_filter: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Full force table: one row per (load case, station, girder pair).

        Returns an empty DataFrame if Vz is absent from the dataset
        (transverse lateral loads not yet modelled).

        Returns
        -------
        pd.DataFrame with columns:
            LoadCase, Station X (m), Girder Pair,
            Fx Left (kN), Fx Right (kN),
            Mz Left (kNm), Mz Right (kNm),
            F_af Left (kN), F_af Right (kN),
            F_total (kN), F_diag (kN), F_chord (kN)
        """
        station_map, girder_map = self._build_station_map()
        all_lcs = self.bridge.result_data["loadcases"]

        if load_case_filter:
            all_lcs = [lc for lc in all_lcs if load_case_filter in str(lc)]

        g_order = list(girder_map.keys())
        if not self.include_edge_beams:
            g_order = [g for g in g_order if g not in ("EB1", "EB2")]

        rows = []
        for lc in all_lcs:
            lc_str = str(lc)
            for sx, g_info in sorted(station_map.items()):
                avail = [g for g in g_order if g in g_info]

                for k in range(len(avail) - 1):
                    g_l, g_r = avail[k], avail[k + 1]
                    info_l   = g_info[g_l]
                    info_r   = g_info[g_r]

                    forces = self._extract_forces(lc, info_l, info_r)
                    if forces is None:
                        continue

                    resolved = self._resolve_forces(forces["f_af_l"], forces["f_af_r"])

                    rows.append({
                        "LoadCase":          lc_str,
                        "Station X (m)":     sx,
                        "Girder Pair":       f"{g_l}-{g_r}",
                        "Vx Left (kN)":      round(forces["fx_l"],   4),
                        "Vx Right (kN)":     round(forces["fx_r"],   4),
                        "Mz Left (kNm)":     round(forces["mz_l"],   4),
                        "Mz Right (kNm)":    round(forces["mz_r"],   4),
                        "F_af Left (kN)":    round(forces["f_af_l"], 4),
                        "F_af Right (kN)":   round(forces["f_af_r"], 4),
                        "F_total (kN)":      resolved["F_total_kN"],
                        "F_diag (kN)":       resolved["F_diag_kN"],
                        "F_chord (kN)":      resolved["F_chord_kN"],
                    })

        return pd.DataFrame(rows)

    def get_critical_forces(self) -> pd.DataFrame:
        """
        Envelope (max absolute) diagonal and chord forces across all load
        cases, per girder pair and per cross-bracing station.

        Returns
        -------
        pd.DataFrame with columns:
            Girder Pair, Station X (m),
            Max |F_diag+torsion| (kN), Governing LC (diag),
            Max |F_chord| (kN),        Governing LC (chord)
        """
        df = self.compute_panel_forces()
        if df.empty:
            return pd.DataFrame()

        diag_col  = "F_diag (kN)"
        chord_col = "F_chord (kN)"
        rows = []
        for (pair, sx), grp in df.groupby(["Girder Pair", "Station X (m)"]):
            idx_d = grp[diag_col].abs().idxmax()
            idx_c = grp[chord_col].abs().idxmax()
            rows.append({
                "Girder Pair":              pair,
                "Station X (m)":            sx,
                "Max |F_diag+torsion| (kN)": round(abs(grp.loc[idx_d, diag_col]), 3),
                "Governing LC (diag)":       grp.loc[idx_d, "LoadCase"],
                "Max |F_chord| (kN)":        round(abs(grp.loc[idx_c, chord_col]), 3),
                "Governing LC (chord)":      grp.loc[idx_c, "LoadCase"],
            })
        return pd.DataFrame(rows)

    def get_design_forces_dict(self) -> dict:
        """
        Structured dict of governing design forces for the design module.

        Per girder pair, takes the WORST station across all stations
        (governing diagonal force and governing chord force may come from
        different stations and load cases).

        Returns
        -------
        dict::

            {
                "brace_type":    "X" or "K",
                "top_chord":     bool,
                "bottom_chord":  bool,
                "geometry": {
                    "girder_spacing_m":  float,
                    "brace_height_m":    float,
                    "girder_depth_m":    float,
                    "diagonal_length_m": float,
                    "alpha_deg":         float,
                    "cb_spacing_m":      float,
                    "depth_ratio":       float,
                },
                "pairs": {
                    "G1-G2": {
                        "max_diagonal_kN":          float,
                        "governing_lc_diag":        str,
                        "critical_station_diag_m":  float,
                        "max_chord_kN":             float,
                        "governing_lc_chord":       str,
                        "critical_station_chord_m": float,
                    },
                    ...
                },
                "overall_max_diagonal_kN": float,
                "overall_max_chord_kN":    float,
            }

        Returns an empty dict when Vz is not yet in the dataset.
        """
        crit = self.get_critical_forces()
        if crit.empty:
            return {}

        # Collapse to one row per girder pair (worst station for each force)
        worst: dict = {}
        for _, row in crit.iterrows():
            pair = row["Girder Pair"]
            f_d  = row["Max |F_diag+torsion| (kN)"]
            f_c  = row["Max |F_chord| (kN)"]
            sx   = row["Station X (m)"]
            lc_d = row["Governing LC (diag)"]
            lc_c = row["Governing LC (chord)"]

            if pair not in worst:
                worst[pair] = {
                    "max_diagonal_kN":          f_d,
                    "governing_lc_diag":        lc_d,
                    "critical_station_diag_m":  sx,
                    "max_chord_kN":             f_c,
                    "governing_lc_chord":       lc_c,
                    "critical_station_chord_m": sx,
                }
            else:
                if f_d > worst[pair]["max_diagonal_kN"]:
                    worst[pair]["max_diagonal_kN"]         = f_d
                    worst[pair]["governing_lc_diag"]       = lc_d
                    worst[pair]["critical_station_diag_m"] = sx
                if f_c > worst[pair]["max_chord_kN"]:
                    worst[pair]["max_chord_kN"]              = f_c
                    worst[pair]["governing_lc_chord"]        = lc_c
                    worst[pair]["critical_station_chord_m"]  = sx

        return {
            "brace_type":             self.brace_type,
            "top_chord":              self.top_chord,
            "bottom_chord":           self.bottom_chord,
            "geometry":               self.get_brace_geometry_info(),
            "pairs":                  worst,
            "overall_max_diagonal_kN": max(v["max_diagonal_kN"] for v in worst.values()),
            "overall_max_chord_kN":    max(v["max_chord_kN"]    for v in worst.values()),
        }

    def get_brace_geometry_info(self) -> dict:
        """Return geometry parameters as a dict (for reporting and design module)."""
        return {
            "brace_type":        self.brace_type,
            "top_chord":         self.top_chord,
            "bottom_chord":      self.bottom_chord,
            "girder_spacing_m":  round(self.s, 4),
            "brace_height_m":    round(self.h, 4),
            "girder_depth_m":    round(self.D, 4),
            "diagonal_length_m": round(self.L_d, 4),
            "horiz_proj_m":      round(self.horiz_proj, 4),
            "alpha_deg":         round(math.degrees(self.alpha_rad), 2),
            "cb_spacing_m":      round(self.cb_spacing, 3),
            "depth_ratio":       self.depth_ratio,
        }

    # =======================================================================
    # PRINT / REPORT METHODS
    # =======================================================================

    def print_configuration(self) -> None:
        """Print brace configuration and geometry."""
        g = self.get_brace_geometry_info()
        print("\n" + "=" * 70)
        print(" " * 18 + "CROSS BRACING CONFIGURATION & GEOMETRY")
        print("=" * 70)
        print(f"  Brace type               : {g['brace_type']}-type")
        print(f"  Top chord                : {'Yes' if g['top_chord'] else 'No'}")
        print(f"  Bottom chord             : {'Yes' if g['bottom_chord'] else 'No'}")
        print("-" * 70)
        print(f"  Girder spacing (s)       : {g['girder_spacing_m']:.4f} m")
        print(f"  Girder depth (D)         : {g['girder_depth_m']:.4f} m")
        print(f"  Brace clear height (h)   : {g['brace_height_m']:.4f} m  "
              f"(depth_ratio = {g['depth_ratio']})")
        if g["brace_type"] == BRACE_K:
            print(f"  Diag. horiz. projection  : {g['horiz_proj_m']:.4f} m  (= s/2)")
        print(f"  Diagonal length          : {g['diagonal_length_m']:.4f} m")
        print(f"  Diagonal angle (alpha)   : {g['alpha_deg']:.2f} deg from horizontal")
        print(f"  Panel spacing            : {g['cb_spacing_m']:.3f} m")
        print("=" * 70)

    def print_panel_forces(
        self,
        load_case_filter: Optional[str] = None,
        max_rows: int = 200,
    ) -> None:
        """Print the full panel-force table (all load cases, stations, pairs)."""
        self.print_configuration()
        df = self.compute_panel_forces(load_case_filter=load_case_filter)
        print("\n" + "=" * 115)
        print(" " * 37 + "CROSS BRACING PANEL FORCES")
        print("=" * 115)
        if df.empty:
            print("  No data — Vz (transverse shear) not yet in dataset, or no load cases found.")
        else:
            print(df.head(max_rows).to_string(index=False))
            if len(df) > max_rows:
                print(f"\n  ... ({len(df) - max_rows} rows omitted; increase max_rows)")
        print("=" * 115)

    def print_critical_forces(self) -> None:
        """Print the design-critical force envelope per girder pair and station."""
        self.print_configuration()
        df = self.get_critical_forces()
        print("\n" + "=" * 115)
        print(" " * 28 + "CROSS BRACING — CRITICAL DESIGN FORCES")
        print("=" * 115)
        if df.empty:
            print("  No critical forces — Vz not in dataset or no load cases found.")
        else:
            print(df.to_string(index=False))
        print("=" * 115)

    def print_station_summary(self, station_x: float, tol: float = 0.5) -> None:
        """
        Print forces for a single cross-bracing station.

        Parameters
        ----------
        station_x : float   Nominal x-position (m).
        tol : float         Search tolerance in metres (default 0.5).
        """
        df = self.compute_panel_forces()
        if df.empty:
            print("No data available.")
            return
        subset = df[(df["Station X (m)"] - station_x).abs() <= tol]
        print(f"\n--- Cross-Bracing Station: x ~ {station_x:.2f} m ---")
        if subset.empty:
            print(f"  No station within ±{tol:.2f} m of x = {station_x:.2f} m.")
        else:
            print(subset.to_string(index=False))
