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

  Step 3  Read Vz from the cross-bracing (transverse) members.
          The grillage forces are in global axes.  The vertical shear
          Vz_i at the i-end (left girder) of the transverse member is
          the direct load the cross-bracing panel carries.
          For a member with no distributed load Vz_i = -Vz_j, so both
          ends carry the same magnitude — using the sum would double-count.

  Step 4  Resolve member forces.

          Resolving Vz_i along the diagonal (angle α from horizontal):

            F_diag  =  Vz_i / cos α

          Chord force equals the full vertical shear:

            F_chord  =  Vz_i

          Sign is preserved: positive → tension, negative → compression.

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
Vz of the transverse (cross-bracing) member is in the global axis,
so it is read directly — no coordinate transformation needed.

For a grillage element with no distributed load, Vz_i = -Vz_j.
Both ends carry the same force magnitude; summing them would
double-count the shear.  Vz_i (left girder end) is used.

  Resolving along the diagonal (α from horizontal):

    F_diag  =  Vz_i / cos α     (kN)

  Chord force:

    F_chord =  Vz_i              (kN)

  where
    Vz_i  (kN)  — Vz at the i-end of the cross-bracing member (left girder)
    α     (rad) — atan(h / horiz_proj)
    Sign preserved: positive = tension, negative = compression

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

import copy
import json
import math
import time
import warnings
from pathlib import Path
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

BRACE_X = "X"
BRACE_K = "K"

_KEY_TOP_CHORD    = "Cross Bracing Top Chord"
_KEY_BOTTOM_CHORD = "Cross Bracing Bottom Chord"


def _fmt_coords(coords) -> str:
    if not coords:
        return "unknown"
    return f"({coords[0]:.3f}, {coords[1]:.3f}, {coords[2]:.3f})"


# ===========================================================================
class CrossBracingForces:
    """
    Step-wise force analysis for X-type or K-type cross bracing.

    Parameters
    ----------
    bridge : PlateGirderBridge
        Fully solved bridge (design() already called).
        bridge.result_data must contain a "crossbracings" key produced by
        results_data_post_processing.post_process().
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

        self._identify_configuration(brace_type, top_chord, bottom_chord)
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
        ai = getattr(self.bridge, "additional_inputs", {})

        if brace_type is not None:
            raw = str(brace_type).strip().upper()
        else:
            raw = str(ai.get(KEY_CROSS_BRACING_TYPE, BRACE_X)).strip().upper()

        if raw not in (BRACE_X, BRACE_K):
            raise ValueError(f"Unsupported brace_type '{raw}'. Choose 'X' or 'K'.")
        self.brace_type: str = raw

        if top_chord is not None:
            self.top_chord = bool(top_chord)
        else:
            val = ai.get(_KEY_TOP_CHORD, "Yes")
            self.top_chord = str(val).strip().lower() not in ("no", "false", "0")

        if bottom_chord is not None:
            self.bottom_chord = bool(bottom_chord)
        else:
            val = ai.get(_KEY_BOTTOM_CHORD, "Yes")
            self.bottom_chord = str(val).strip().lower() not in ("no", "false", "0")

    # =======================================================================
    # STEP 2 — BRACE GEOMETRY
    # =======================================================================

    def _init_geometry(self, cb_spacing: Optional[float]) -> None:
        sizing = getattr(self.bridge, "sizing_result", None)
        geom   = getattr(self.bridge, "grillage_geometry", None)

        if sizing is None or geom is None:
            raise RuntimeError(
                "CrossBracingForces requires bridge.design() to have been called first."
            )

        if cb_spacing is not None:
            self.cb_spacing = float(cb_spacing)
        else:
            ai = getattr(self.bridge, "additional_inputs", {})
            self.cb_spacing = float(
                ai.get(KEY_CROSS_BRACING_SPACING, DEFAULT_CROSS_BRACING_SPACING)
            )

        sp = self.bridge.section_props
        self.D      = float(sp["D"])
        self.tf_top = float(sp.get("t_f_top", 0.0))
        self.tf_bot = float(sp.get("t_f_bot", self.tf_top))

        self.h = self.D * self.depth_ratio
        self.s = float(sizing.girder_spacing)
        self.L = float(geom.L)

        if self.brace_type == BRACE_X:
            self.horiz_proj = self.s
        else:
            self.horiz_proj = self.s / 2.0

        self.L_d      = math.sqrt(self.horiz_proj ** 2 + self.h ** 2)
        self.alpha_rad = math.atan2(self.h, self.horiz_proj)
        self.cos_alpha = math.cos(self.alpha_rad)

    # =======================================================================
    # STEP 3 — BUILD CHAIN MAP FROM crossbracings
    # =======================================================================

    def _build_chain_map(self) -> list:
        """
        Read each cross-bracing chain from result_data["crossbracings"].

        left_girder, right_girder, and connection coordinates are already
        stored on each chain by results_data_post_processing.build_crossbracings
        — no re-derivation needed here.

        Returns
        -------
        chain_stations : list[dict]
            [{ "start_coords", "end_coords",
               "first_member", "last_member",
               "left_girder",  "right_girder" }, ...]
        """
        rd        = self.bridge.result_data
        cb_chains = rd.get("crossbracings", [])

        chain_stations = []
        for chain in cb_chains:
            mems = chain.get("members", [])
            if not mems:
                continue

            left_girder  = chain.get("left_girder")
            right_girder = chain.get("right_girder")
            if left_girder is None or right_girder is None:
                continue

            start = chain.get("start") or {}
            end   = chain.get("end")   or {}

            chain_stations.append({
                "start_coords": start.get("coords"),
                "end_coords":   end.get("coords"),
                "first_member": str(mems[0]),
                "last_member":  str(mems[-1]),
                "left_girder":  left_girder,
                "right_girder": right_girder,
            })

        return chain_stations

    # =======================================================================
    # STEP 3 (cont.) — READ Vz FROM TRANSVERSE MEMBER
    # =======================================================================

    def _read_vz(self, lc: str, member_id: str, is_i: bool) -> Optional[float]:
        """
        Read Vz_i or Vz_j from a transverse (cross-bracing) member.
        Forces are in global axes so Vz is used directly.

        Returns float in N, or None if absent.
        """
        comp = "Vz_i" if is_i else "Vz_j"
        try:
            return float(
                self.bridge.result_data["forces"][str(lc)][member_id][comp]
            )
        except (KeyError, TypeError, ValueError):
            return None

    # =======================================================================
    # STEP 4 — RESOLVE MEMBER FORCES
    # =======================================================================

    def _resolve_forces(self, vz_kn: float) -> dict:
        """
        Resolve Vz_i (left-girder end shear, kN) into diagonal and chord forces.

          F_diag  =  Vz_i / cos α   — axial force in diagonal
          F_chord =  Vz_i            — axial force in chord

        Sign preserved: positive = tension, negative = compression.
        """
        if self.cos_alpha < 1e-9:
            return {"F_diag_kN": 0.0, "F_chord_kN": 0.0}

        return {
            "F_diag_kN":  round(vz_kn / self.cos_alpha, 4),
            "F_chord_kN": round(vz_kn, 4),
        }

    # =======================================================================
    # STEP 5 — TABULATE AND ENVELOPE FOR DESIGN
    # =======================================================================

    def compute_panel_forces(
        self,
        load_case_filter: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Full force table: one row per (load case, cross-bracing chain).

        Returns
        -------
        pd.DataFrame with columns:
            LoadCase, Station X (m), Girder Pair,
            Vz_i (kN), Vz_j (kN), F_diag (kN), F_chord (kN)
        """
        chain_stations = self._build_chain_map()
        all_lcs = self.bridge.result_data["loadcases"]

        if load_case_filter:
            all_lcs = [lc for lc in all_lcs if load_case_filter in str(lc)]

        _eq_tol = 1e-3  # kN tolerance for Vz_i + Vz_j == 0 check

        rows = []
        for lc in all_lcs:
            lc_str = str(lc)
            for st in chain_stations:

                if not self.include_edge_beams:
                    if st["left_girder"] in ("EB1", "EB2") or \
                       st["right_girder"] in ("EB1", "EB2"):
                        continue

                # Vz_i of first member, Vz_j of last member (global axis)
                vz_l = self._read_vz(lc_str, st["first_member"], is_i=True)
                vz_r = self._read_vz(lc_str, st["last_member"],  is_i=False)

                if vz_l is None or vz_r is None:
                    continue

                vz_l_kn = vz_l / 1e3
                vz_r_kn = vz_r / 1e3

                # Vz_i = -Vz_j must hold for a member with no distributed load
                warning_msg = None
                if abs(vz_l_kn + vz_r_kn) > _eq_tol:
                    warning_msg = (
                        f"Member {st['first_member']} LC '{lc_str}': "
                        f"Vz_i={vz_l_kn:.4f} kN, Vz_j={vz_r_kn:.4f} kN — "
                        f"expected Vz_i = -Vz_j (diff={vz_l_kn + vz_r_kn:.4f} kN)"
                    )
                    warnings.warn(
                        f"[CrossBracingForces] Equilibrium violated — {warning_msg}",
                        stacklevel=2,
                    )

                resolved = self._resolve_forces(vz_l_kn)

                rows.append({
                    "LoadCase":    lc_str,
                    "Girder Pair": f"{st['left_girder']}-{st['right_girder']}",
                    "Left Coords": _fmt_coords(st["start_coords"]),
                    "Right Coords": _fmt_coords(st["end_coords"]),
                    "Vz_i (kN)":   round(vz_l_kn, 4),
                    "Vz_j (kN)":   round(vz_r_kn, 4),
                    "F_diag (kN)": resolved["F_diag_kN"],
                    "F_chord (kN)": resolved["F_chord_kN"],
                    "Warning":     warning_msg,
                })

        return pd.DataFrame(rows)

    def get_critical_forces(self) -> pd.DataFrame:
        """
        Envelope (max absolute) diagonal and chord forces across all load
        cases, per girder pair and station.

        Returns
        -------
        pd.DataFrame with columns:
            Girder Pair, Station X (m),
            Max |F_diag| (kN), Governing LC (diag),
            Max |F_chord| (kN), Governing LC (chord)
        """
        df = self.compute_panel_forces()
        if df.empty:
            return pd.DataFrame()

        diag_col  = "F_diag (kN)"
        chord_col = "F_chord (kN)"
        rows = []
        for (pair, lc, rc), grp in df.groupby(
            ["Girder Pair", "Left Coords", "Right Coords"]
        ):
            idx_d = grp[diag_col].abs().idxmax()
            idx_c = grp[chord_col].abs().idxmax()
            rows.append({
                "Girder Pair":          pair,
                "Left Coords":          lc,
                "Right Coords":         rc,
                "Max |F_diag| (kN)":    round(abs(grp.loc[idx_d, diag_col]),  3),
                "Governing LC (diag)":  grp.loc[idx_d, "LoadCase"],
                "Max |F_chord| (kN)":   round(abs(grp.loc[idx_c, chord_col]), 3),
                "Governing LC (chord)": grp.loc[idx_c, "LoadCase"],
                "Warning (diag)":       grp.loc[idx_d, "Warning"],
                "Warning (chord)":      grp.loc[idx_c, "Warning"],
            })
        return pd.DataFrame(rows)

    def get_design_forces_dict(self) -> dict:
        """
        Governing design forces per girder pair, with force type (Tension/Compression).

        Compression is preferred over tension of equal magnitude because steel
        members buckle before yielding — compressive capacity is lower.

        Returns
        -------
        dict::

            {
                "brace_type":   "X" or "K",
                "top_chord":    bool,
                "bottom_chord": bool,
                "geometry":     { ... },
                "pairs": {
                    "G1-G2": {
                        "gov_diag_kN":   float,
                        "gov_diag_type": "Tension" or "Compression",
                        "gov_chord_kN":  float,
                        "gov_chord_type": "Tension" or "Compression",
                    },
                    ...
                },
            }
        """
        df = self.compute_panel_forces()
        if df.empty:
            return {}

        diag_col  = "F_diag (kN)"
        chord_col = "F_chord (kN)"

        pairs: dict = {}
        for pair, grp in df.groupby("Girder Pair"):
            max_tens_diag  = float(grp[diag_col].max())
            max_comp_diag  = float(grp[diag_col].min())
            max_tens_chord = float(grp[chord_col].max())
            max_comp_chord = float(grp[chord_col].min())

            if abs(max_comp_diag) >= abs(max_tens_diag):
                gov_diag, gov_diag_type = abs(max_comp_diag), "Compression"
            else:
                gov_diag, gov_diag_type = abs(max_tens_diag), "Tension"

            if abs(max_comp_chord) >= abs(max_tens_chord):
                gov_chord, gov_chord_type = abs(max_comp_chord), "Compression"
            else:
                gov_chord, gov_chord_type = abs(max_tens_chord), "Tension"

            pairs[pair] = {
                "gov_diag_kN":    round(gov_diag,  3),
                "gov_diag_type":  gov_diag_type,
                "gov_chord_kN":   round(gov_chord, 3),
                "gov_chord_type": gov_chord_type,
            }

        return {
            "brace_type":   self.brace_type,
            "top_chord":    self.top_chord,
            "bottom_chord": self.bottom_chord,
            "geometry":     self.get_brace_geometry_info(),
            "pairs":        pairs,
        }

    def get_brace_geometry_info(self) -> dict:
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

    def get_crossbracing_count(self) -> int:
        """Return the number of cross-bracing panels in result_data."""
        return len(self.bridge.result_data.get("crossbracings", []))

    def run_member_designs(self, forces_dict: dict, dev: bool = False) -> tuple[list, list]:
        """
        Run Osdag member designs for diagonals and chords.

        Parameters
        ----------
        forces_dict : dict
            Output of get_design_forces_dict().
        dev : bool
            If True, dump forces_dict as JSON to tools/crossbracing_forces_dict.json.

        Returns
        -------
        (diag_results, chord_results) — one Osdag output dict per girder pair.
        """
        if dev:
            out = Path(__file__).parents[5] / "tools" / "crossbracing_forces_dict.json"
            out.write_text(json.dumps(forces_dict, indent=2))
            print(f"[CrossBracing] dev dump → {out}")

        from osdagbridge.core.utils.connect import (
            design_dict_struts_bolted,
            design_dict_tension_bolted,
            run_parallel_designs,
        )

        if not forces_dict or not forces_dict.get("pairs"):
            return [], []

        geom       = forces_dict.get("geometry", {})
        L_diag_mm  = round(geom.get("diagonal_length_m", 0) * 1000)
        L_chord_mm = round(geom.get("horiz_proj_m",      0) * 1000)

        diag_dicts:  list = []
        chord_dicts: list = []

        for pair, vals in forces_dict["pairs"].items():
            base_diag  = (design_dict_tension_bolted if vals["gov_diag_type"]  == "Tension"
                          else design_dict_struts_bolted)
            base_chord = (design_dict_tension_bolted if vals["gov_chord_type"] == "Tension"
                          else design_dict_struts_bolted)

            d = copy.deepcopy(base_diag)
            d["Load.Axial"]    = str(float(vals["gov_diag_kN"]))
            d["Member.Length"] = str(L_diag_mm)
            diag_dicts.append(d)

            d = copy.deepcopy(base_chord)
            d["Load.Axial"]    = str(float(vals["gov_chord_kN"]))
            d["Member.Length"] = str(L_chord_mm)
            chord_dicts.append(d)

        # Submit diagonals + chords in one batch so all run concurrently.
        all_dicts = diag_dicts + chord_dicts
        if not all_dicts:
            return [], []

        n_diag = len(diag_dicts)
        sep    = "-" * 60
        print(
            f"\n{sep}\n"
            f"  CROSS BRACING DESIGNS  ({n_diag} pair(s))"
            f"  diag L={L_diag_mm} mm  chord L={L_chord_mm} mm\n"
            f"{sep}"
        )
        t0         = time.perf_counter()
        all_results = run_parallel_designs(all_dicts)
        print(f"  Total time : {time.perf_counter() - t0:.3f}s  |  {len(all_dicts)} designs\n{sep}")

        diag_results  = all_results[:n_diag]
        chord_results = all_results[n_diag:]

        return diag_results, chord_results

    # =======================================================================
    # PRINT / REPORT METHODS
    # =======================================================================

    def print_configuration(self) -> None:
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
        self.print_configuration()
        df = self.compute_panel_forces(load_case_filter=load_case_filter)
        print("\n" + "=" * 95)
        print(" " * 30 + "CROSS BRACING PANEL FORCES")
        print("=" * 95)
        if df.empty:
            print("  No data — Vz absent from dataset or no load cases found.")
        else:
            print(df.head(max_rows).to_string(index=False))
            if len(df) > max_rows:
                print(f"\n  ... ({len(df) - max_rows} rows omitted; increase max_rows)")
        print("=" * 95)

    def print_critical_forces(self) -> None:
        self.print_configuration()
        df = self.get_critical_forces()
        print("\n" + "=" * 95)
        print(" " * 22 + "CROSS BRACING — CRITICAL DESIGN FORCES")
        print("=" * 95)
        if df.empty:
            print("  No critical forces — Vz not in dataset or no load cases found.")
        else:
            print(df.to_string(index=False))
        print("=" * 95)

    def print_station_summary(self, station_x: float, tol: float = 0.5) -> None:
        df = self.compute_panel_forces()
        if df.empty:
            print("No data available.")
            return

        def _x_from_coords(s: str) -> float:
            try:
                return float(s.strip("()").split(",")[0])
            except Exception:
                return float("nan")

        mask = df["Left Coords"].apply(_x_from_coords).sub(station_x).abs() <= tol
        subset = df[mask]
        print(f"\n--- Cross-Bracing Station: x ~ {station_x:.2f} m ---")
        if subset.empty:
            print(f"  No station within ±{tol:.2f} m of x = {station_x:.2f} m.")
        else:
            print(subset.to_string(index=False))
