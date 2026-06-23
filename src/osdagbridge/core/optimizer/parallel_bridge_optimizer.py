"""
parallel_bridge_optimizer.py
----------------------------
Parallel, multiprocessing weight-minimisation of a plate-girder bridge
superstructure, built on the generic engine in ``parallel_optimizer.py``.

Design vector
-------------
    x = [n, s, t_slab, D, bf, tf, tw]
        n       number of girders                (count)
        s       girder spacing                   (m)
        t_slab  deck-slab thickness              (mm)
        D       girder overall depth             (mm)
        bf      flange width (symmetric I)       (mm)
        tf      flange thickness (symmetric I)   (mm)
        tw      web thickness                    (mm)

How it differs from the sequential ``bridge_optimizer.py``
---------------------------------------------------------
  * No module globals. All per-run context (span, width, densities, the base
    ``input_dict``) lives in a single picklable ``OptiConfig`` that is installed
    into every worker process **once** via the pool ``initializer`` - not shipped
    with every candidate, not read from globals that don't exist in a child.
  * The fitness function is a module-level, picklable callable and folds the
    feasibility check and the weight objective into one cross-process call,
    returning ``+inf`` for any infeasible / failing candidate.
  * Every candidate goes through the SAME real flow a single design uses:
    inputs -> ANALYSER (grillage analysis) -> DESIGNER (IRC 22:2015 girder DCR) ->
    DECK (full IRC deck-slab design). Feasible only if the girder DCR does not FAIL
    and every deck utilisation ratio <= 1. This is the accurate (~10 s/candidate)
    path, run in parallel across candidates. CAD is generated once, in the parent
    process, for the winning design only.

Usage
-----
    from osdagbridge.core.optimizer.parallel_bridge_optimizer import optimize_parallel

    bridge = optimize_parallel(base_input_dict)   # a fully solved input_dict
    bridge.design()                               # full pipeline + CAD on the winner
"""

from __future__ import annotations

import copy
import gc
import math
import sys
from dataclasses import dataclass, replace
from typing import List, Optional

import numpy as np

from osdagbridge.core.bridge_types.plate_girder.plategirderbridge import PlateGirderBridge
from osdagbridge.core.bridge_types.plate_girder.designer import SteelSection
from osdagbridge.core.utils.common import (
    KEY_SPAN,
    KEY_CARRIAGEWAY_WIDTH,
    KEY_TS_OVERALL_WIDTH,
    KEY_TS_NO_OF_GIRDERS,
    KEY_TS_GIRDER_SPACING,
    KEY_TS_DECK_OVERHANG,
    KEY_TS_DECK_THICKNESS,
    KEY_WC_THICKNESS,
    KEY_MP_CB_SPACING,
    KEY_DESIGN_MODE,
    KEY_MP_GIRDER_DEPTH,
    KEY_MP_GIRDER_WEB_DEPTH,
    KEY_MP_GIRDER_WEB_THICKNESS,
    KEY_MP_GIRDER_TOP_FLANGE_WIDTH,
    KEY_MP_GIRDER_TOP_FLANGE_THICKNESS,
    KEY_MP_GIRDER_BOTTOM_FLANGE_WIDTH,
    KEY_MP_GIRDER_BOTTOM_FLANGE_THICKNESS,
)

from .parallel_optimizer import ParallelOptimizer, OptimisationResult


# ------------------------------------------------------------------------------
#  Constants
# ------------------------------------------------------------------------------

# IS 2062 standard plate thickness list (mm) — tf and tw must come from here.
_STD_PLATES: List[int] = [
    6, 8, 10, 12, 14, 16, 18, 20, 22, 25,
    28, 32, 36, 40, 45, 50, 56, 63, 70, 80, 90, 100,
]

# Design mode that makes the pipeline treat girder dims as fixed numeric values
# (the candidate's), as opposed to "Optimized" which expects bounds.
_FIXED_DESIGN_MODE = "Custom"

# Steel elastic / shear modulus (MPa) for the analytical LTB capacity.
E_steel = 200000.0
G_steel = 79300.0

# Analytical demand model defaults (used by candidate_is_feasible).
_RHO_WEARING_COURSE = 22.0   # kN/m^3  — wearing course unit weight
_LIVE_UDL_kN_m2     = 5.0    # kN/m^2  — nominal carriageway live-load allowance
_GAMMA_DL           = 1.35   # ULS factor on permanent load
_GAMMA_LL           = 1.50   # ULS factor on live load
_FY_DEFAULT         = 250.0  # MPa     — girder yield (fallback)

# --- Gate 0: analytical girder pre-screen ------------------------------------
# Reject a candidate BEFORE the (expensive) grillage analysis only when its
# girder is clearly too weak even for a crude simply-supported demand estimate.
# The estimate deliberately OVER-states demand (full bay to one girder, no
# composite action, no load distribution), so the real grillage demand is lower
# — meaning a section that clears this gate is comfortably feasible, and we only
# reject sections far below it. _PRESCREEN_MARGIN is the buffer: reject iff
# capacity < demand * MARGIN (so MARGIN=0.70 ⇒ reject only at <70% of the crude
# demand). Lower MARGIN = more conservative (fewer rejects). MUST be calibrated
# empirically (feasible-set containment vs the no-gate baseline).
_ENABLE_PRESCREEN   = True
_PRESCREEN_MARGIN   = 0.70
# Yield used for the pre-screen capacity. Kept high (E350, the common girder
# grade and the designer default) so capacity is never UNDER-stated — an
# under-stated capacity could wrongly reject a valid design. Over-stating it is
# safe: at worst a hopeless design slips through to the full analysis.
_FY_PRESCREEN       = 350.0


# ------------------------------------------------------------------------------
#  Analytical girder section (capacity formulas ported from trial_optimizer.py)
# ------------------------------------------------------------------------------

class GirderSection(SteelSection):
    """
    Symmetric plate-girder I-section with closed-form IRC 24 / IS 800 capacity
    formulas (moment with LTB reduction, shear). Lets a candidate be checked by
    formula in microseconds instead of a full grillage analysis.
    """

    def __init__(self, D: float = 0.0, bf: float = 0.0, tf: float = 0.0, tw: float = 0.0):
        super().__init__(D, bf, tf, bf, tf, tw)   # symmetric: top == bottom flange
        self.tf = tf
        self.bf = bf

    @property
    def Iy_steel(self) -> float:
        I_flange = self.tf * (2 * self.bf) ** 3 / 12
        I_web    = self.dw * self.tw ** 3 / 12
        return round(I_web + 2 * I_flange, 3)

    @property
    def I_warping(self) -> float:
        # beta_t = 0.5 for a doubly-symmetric I section
        return round(0.5 * 0.5 * self.Iy_steel * (self.D - self.tf) ** 2, 3)

    @property
    def I_torsion(self) -> float:
        I_flange = self.bf * self.tf ** 3 / 3
        I_web    = self.dw * self.tw ** 3 / 3
        return round(I_web + 2 * I_flange, 3)

    def self_weight(self, span_m: float, rho_steel: float = 78.5) -> float:
        """Self-weight of one girder over the span (kN)."""
        return (self.A_steel * span_m / 1e6) * rho_steel

    def M_cr(self, L: float = 1.0) -> float:
        """Elastic critical (LTB) moment for unbraced length L (m). Returns N-mm."""
        L_eff = L * 1000.0   # mm
        M_cr  = math.pi ** 2 * E_steel * self.Iy_steel / L_eff ** 2
        M_cr *= G_steel * self.I_torsion + math.pi ** 2 * E_steel * self.I_warping / L_eff ** 2
        return round(math.sqrt(M_cr), 3)

    def moment_capacity(self, span_length_m: float, unbraced_length: float = 1.0,
                        fy_steel: float = 250.0) -> float:
        """Design moment capacity with LTB reduction, net of self-weight moment (kN-m)."""
        M_cr      = self.M_cr(unbraced_length)
        lambda_LT = math.sqrt(min(self.Zp_steel, 1.2 * self.Ze_steel) * fy_steel / M_cr)
        alpha     = 0.49 if self.fabrication == "welded" else 0.21
        phi_LT    = 0.5 * (1 + alpha * (lambda_LT - 0.2) + lambda_LT ** 2)
        x_LT      = 1 / (phi_LT + math.sqrt(phi_LT ** 2 - lambda_LT ** 2))
        M_d       = round(self.Zp_steel * x_LT * fy_steel / (1.1 * 1e6), 4)
        return M_d - (self.self_weight(span_length_m) * span_length_m * 0.125)

    def shear_capacity(self, span_length_m: float, fy_steel: float = 250.0) -> float:
        """Design shear capacity, net of self-weight shear (kN)."""
        shear_area = self.Aw if self.fabrication == "welded" else self.D * self.tw
        V_d        = round(shear_area * fy_steel / (math.sqrt(3) * 1.1 * 1000), 4)
        return V_d - self.self_weight(span_length_m)


# ------------------------------------------------------------------------------
#  Rounding helpers (snap continuous DE variables onto manufacturable values)
# ------------------------------------------------------------------------------

def _ceiling_plate(value_mm: float) -> float:
    """Smallest IS 2062 plate thickness >= value_mm (always structurally safe)."""
    for p in _STD_PLATES:
        if p >= value_mm:
            return float(p)
    return float(_STD_PLATES[-1])


def _ceil10(v: float) -> float:
    """Ceil to nearest 10 mm."""
    return float(math.ceil(v / 10.0) * 10.0)


def _round5(v: float) -> float:
    """Round to nearest 5 mm."""
    return float(round(v / 5.0) * 5.0)


def clamp(v: float, lo: float, hi: float) -> float:
    """Hard-clip v to [lo, hi]."""
    return max(lo, min(v, hi))


# ------------------------------------------------------------------------------
#  Per-run configuration (picklable; installed into every worker once)
# ------------------------------------------------------------------------------

@dataclass
class OptiConfig:
    """Everything a worker needs to evaluate a candidate. Must stay picklable."""
    base_input_dict : dict
    span_m          : float
    deck_width_m    : float
    steel_density   : float = 78.5   # kN/m^3
    concrete_density: float = 25.0   # kN/m^3
    # Search-phase moving-load fidelity: when set, the analyser samples the
    # vehicle at these critical reference x-positions instead of the full 50-step
    # uniform sweep. None ⇒ full fidelity (used for the final winner re-check).
    search_positions_x: Optional[np.ndarray] = None

    @classmethod
    def from_input_dict(
        cls,
        base_input_dict : dict,
        steel_density   : float = 78.5,
        concrete_density: float = 25.0,
        search_positions_x: Optional[np.ndarray] = None,
    ) -> "OptiConfig":
        return cls(
            base_input_dict  = copy.deepcopy(base_input_dict),
            span_m           = float(base_input_dict[KEY_SPAN]),
            deck_width_m     = float(base_input_dict[KEY_TS_OVERALL_WIDTH]),
            steel_density    = steel_density,
            concrete_density = concrete_density,
            search_positions_x = search_positions_x,
        )


# ------------------------------------------------------------------------------
#  Candidate normalisation: raw DE vector -> manufacturable, code-compliant dims
# ------------------------------------------------------------------------------

@dataclass
class Candidate:
    n       : int
    s       : float       # m
    overhang: float       # m
    t_slab  : float       # mm
    D       : float       # mm
    bf      : float       # mm
    tf      : float       # mm
    tw      : float       # mm
    dw      : float       # mm
    section : SteelSection


def normalize_candidate(x: np.ndarray, cfg: OptiConfig) -> Candidate:
    """
    Snap a raw DE vector onto a valid, manufacturable section + layout.

    Mirrors the snapping rules of the sequential optimiser but is pure: it reads
    only ``x`` and ``cfg`` (no globals), so it is safe to call inside a worker.
    """
    n, s, t_slab, D, bf, tf, tw = (
        x[0], x[1], x[2], x[3], x[4], x[5], x[6]
    )

    span  = cfg.span_m
    width = cfg.deck_width_m

    # Layout ----------------------------------------------------------------
    # Spacing is NOT an independent variable: for a fixed overall deck width the
    # girders are equally spaced, so spacing is fully determined by the girder
    # count. This matches the bridge layout solver's convention
    #   overall_width = n * spacing,  overhang = spacing / 2
    # (e.g. 8.4 m / 4 girders => 2.1 m spacing, 1.05 m overhang). The raw `s`
    # gene is intentionally ignored here so the CAD geometry is always physical.
    n        = int(max(2, round(n)))
    s        = width / n
    overhang = 0.5 * s
    t_slab   = _round5(clamp(t_slab, 150.0, 400.0))

    # Depth — respect span/depth ratio bounds, snap to 10 mm.
    D_lo = (span / 25.0) * 1000.0
    D_hi = (span / 15.0) * 1000.0
    D    = _ceil10(clamp(D, D_lo, D_hi))

    # Flange width — keep as a fraction of D so scaling stays proportional.
    bf_frac = clamp(bf / D, 0.20, 0.40)
    bf      = _round5(bf_frac * D)

    # Flange thickness — IS 2062 plate; shrink one step at a time if the web
    # depth collapses to <= 0.
    tf = _ceiling_plate(clamp(tf, 6.0, 100.0))
    dw = D - 2.0 * tf
    while dw <= 0.0 and tf > float(_STD_PLATES[0]):
        idx = _STD_PLATES.index(int(tf)) if int(tf) in _STD_PLATES else len(_STD_PLATES) - 1
        tf  = float(_STD_PLATES[max(0, idx - 1)])
        dw  = D - 2.0 * tf

    # Web thickness — IS 2062 plate; enforce slenderness minimum tw >= dw/250.
    min_tw = clamp(max(dw / 250.0, 6.0), 6.0, 40.0)
    tw     = max(_ceiling_plate(clamp(tw, 6.0, 40.0)), _ceiling_plate(min_tw))
    tw     = clamp(tw, 6.0, 40.0)

    section = SteelSection(D, bf, tf, bf, tf, tw)
    return Candidate(n, s, overhang, t_slab, D, bf, tf, tw, dw, section)


def candidate_weight_components(cand: Candidate, cfg: OptiConfig) -> dict:
    """
    Per-component superstructure weight breakdown (kN). Keeps the objective and
    the user-facing breakdown in one place so they can never disagree.

      steel_girders_kN : all N girders, A_steel x span x rho_steel
      deck_concrete_kN : deck slab volume x rho_concrete
      total_kN         : the optimisation objective
    """
    span  = cfg.span_m
    width = cfg.deck_width_m
    w_steel    = cand.n * cand.section.A_steel * span * cfg.steel_density / 1e6
    w_concrete = cand.t_slab * width * span * cfg.concrete_density / 1e3
    return {
        "steel_girders_kN": w_steel,
        "deck_concrete_kN": w_concrete,
        "total_kN":         w_steel + w_concrete,
    }


def candidate_weight(cand: Candidate, cfg: OptiConfig) -> float:
    """Proportional superstructure weight (kN): girder steel + deck concrete."""
    return candidate_weight_components(cand, cfg)["total_kN"]


def _girder_prescreen(cand: Candidate, cfg: OptiConfig) -> bool:
    """
    GATE 0 — microsecond analytical feasibility check, BEFORE any grillage build.

    Estimate the ULS moment & shear demand on the most-loaded girder with a crude
    simply-supported beam (M = wL²/8, V = wL/2) and compare against the closed-form
    ``GirderSection`` capacities. Returns True ("keep — run the full analysis") and
    only False ("reject — clearly too weak") when the section is below the demand
    by the ``_PRESCREEN_MARGIN`` buffer.

    The demand model intentionally over-states load (one full deck bay carried by a
    single girder, no composite action, no transverse distribution), so the true
    grillage demand is lower. That, plus the margin and a non-under-stated yield,
    makes a false reject extremely unlikely: we skip the analysis only for designs
    no realistic distribution could save. Disabled wholesale by ``_ENABLE_PRESCREEN``.
    """
    if not _ENABLE_PRESCREEN:
        return True
    try:
        span  = cfg.span_m
        width = cfg.deck_width_m

        # Tributary load width to one girder, and a conservative live-load share.
        w_trib = width / cand.n
        lldf   = 2.0 / cand.n                      # worst girder takes ~2/n of carriageway LL

        t_slab_m = cand.t_slab / 1000.0
        wc_m     = float(cfg.base_input_dict.get(KEY_WC_THICKNESS, 50.0)) / 1000.0

        # UDL on the worst girder (kN/m).
        w_slab = t_slab_m * w_trib * cfg.concrete_density
        w_wc   = wc_m     * w_trib * _RHO_WEARING_COURSE
        w_self = cand.section.A_steel / 1e6 * cfg.steel_density
        w_dl   = w_slab + w_wc + w_self
        w_ll   = _LIVE_UDL_kN_m2 * w_trib * lldf

        w_u = _GAMMA_DL * w_dl + _GAMMA_LL * w_ll  # factored UDL (kN/m)
        m_demand = w_u * span * span / 8.0         # kN·m
        v_demand = w_u * span / 2.0                # kN

        # Capacities from the existing closed-form formulas (kN·m, kN).
        cb_spacing = float(cfg.base_input_dict.get(KEY_MP_CB_SPACING, span) or span)
        gsec = GirderSection(cand.D, cand.bf, cand.tf, cand.tw)
        m_cap = gsec.moment_capacity(span, unbraced_length=cb_spacing, fy_steel=_FY_PRESCREEN)
        v_cap = gsec.shear_capacity(span, fy_steel=_FY_PRESCREEN)

        # Reject only when clearly under-strength (capacity below the buffered demand).
        if m_cap < m_demand * _PRESCREEN_MARGIN:
            return False
        if v_cap < v_demand * _PRESCREEN_MARGIN:
            return False
        return True
    except Exception:
        # Never let a pre-screen error reject a design — fall through to full analysis.
        return True


def critical_path_positions_x(span: float, max_len: float) -> np.ndarray:
    """
    A small set of *critical* moving-load reference positions for the SEARCH phase,
    replacing the uniform 50-step sweep. The vehicle reference point travels the
    path x ∈ [-max_len, span + max_len]; we sample where the response peaks:

      * 2 positions near EACH support (governs max shear),
      * a 4-point cluster near mid-span / live-load CG (governs max moment — placed
        slightly off-centre, not exactly mid-span).

    The winning design is later re-checked with the full 50-step sweep.
    """
    near = 0.10 * span
    xs = [
        0.0, near,                 # left support region (max shear)
        span - near, span,         # right support region (max shear)
        0.42 * span, 0.46 * span,  # mid cluster (max moment, off-centre toward heavy axles)
        0.50 * span, 0.54 * span,
    ]
    return np.array(sorted(xs), dtype=float)


def candidate_input_dict(cand: Candidate, cfg: OptiConfig) -> dict:
    """
    Build a full ``input_dict`` for this candidate: deep-copy the solved base
    dict, switch to fixed (Custom) mode, and overwrite the layout + girder
    section keys (girder dims in **mm**; the pipeline converts to metres).
    """
    d = copy.deepcopy(cfg.base_input_dict)

    d[KEY_DESIGN_MODE]        = _FIXED_DESIGN_MODE
    d[KEY_TS_NO_OF_GIRDERS]   = cand.n
    d[KEY_TS_GIRDER_SPACING]  = cand.s
    d[KEY_TS_DECK_OVERHANG]   = cand.overhang
    d[KEY_TS_DECK_THICKNESS]  = cand.t_slab

    # Girder dims, in mm. resolve_girder_value() resolves the un-suffixed base
    # key first, but per-girder G{i}.M1 keys are set too so every girder picks
    # up the same symmetric section regardless of how consumers index it.
    dims_mm = {
        KEY_MP_GIRDER_DEPTH:                   cand.D,
        KEY_MP_GIRDER_WEB_DEPTH:               cand.dw,
        KEY_MP_GIRDER_TOP_FLANGE_WIDTH:        cand.bf,
        KEY_MP_GIRDER_BOTTOM_FLANGE_WIDTH:     cand.bf,
        KEY_MP_GIRDER_TOP_FLANGE_THICKNESS:    cand.tf,
        KEY_MP_GIRDER_BOTTOM_FLANGE_THICKNESS: cand.tf,
        KEY_MP_GIRDER_WEB_THICKNESS:           cand.tw,
    }
    # Force a numeric value for the base key AND every per-girder suffix the base
    # dict already carries (e.g. ".G1.M1") — so no Optimized-mode "All" string can
    # survive for a girder dimension (which would crash classify_section with
    # "int - str"). Also guarantee G1..Gn exist even if the base lacked them.
    for base_key, raw in dims_mm.items():
        val    = float(raw)
        prefix = base_key + "."
        d[base_key] = val
        for k in list(d.keys()):
            if k.startswith(prefix):
                d[k] = val
        for gi in range(cand.n):
            d[f"{base_key}.G{gi + 1}.M1"] = val

    return d


# ------------------------------------------------------------------------------
#  Feasibility via the REAL flow every single design goes through:
#     inputs -> analyser (analyser.py) -> designer (designer.py) -> deck design
#  (same as PlateGirderBridge.design(), but WITHOUT CAD — CAD is only built once,
#   for the winning design). ~10 s per candidate; that is intentional.
# ------------------------------------------------------------------------------

class _NullWriter:
    def write(self, _): pass
    def flush(self): pass


def reset_design(bridge: "Optional[PlateGirderBridge]") -> None:
    """
    Clear ONE evaluated design from memory.

    A single candidate produces a fully analysed ``PlateGirderBridge`` that holds
    large objects — the grillage model, the xarray result datasets, the IRC 22
    DCR engine, the deck-design output, the load-effect / deflection caches — and
    leaves this design's nodes / elements / loads in the OpenSees C++ global
    domain. None of that is needed once the design's details have been recorded.
    Across a 200-design run it would otherwise pile up, so we wipe it *per design*:
    design 1 is freed the instant its details are saved, then design 2 is built,
    evaluated, freed, and so on. Memory stays flat instead of growing with N.

      1. Wipe the OpenSees global domain (process-global C++ state shared by every
         design in this worker).
      2. Detach the bridge's heavy attributes so the Python objects — which form
         reference cycles via numpy / xarray and so survive plain refcounting —
         become collectable.
      3. Force a gc pass to reclaim them now, not at some later collection.

    Safe to call with ``bridge is None`` or on a partially built bridge: every
    step is best-effort.
    """
    # 1. OpenSees C++ global domain — shared across all designs in this process.
    try:
        import openseespy.opensees as ops
        ops.wipe()
    except Exception:
        pass

    # 2. Drop this design's heavy Python state.
    if bridge is not None:
        gm = getattr(bridge, "grillage_model", None)
        if gm is not None:
            # the ospgrillage / OpenSees model object and its dataset
            for attr in ("model", "_deduplicated_results", "_results"):
                try:
                    setattr(gm, attr, None)
                except Exception:
                    pass
        for attr in (
            "grillage_model", "result_data", "_results_with_envelope",
            "_dcr_engine", "design_results", "output_dict",
            "_load_effects_cache", "_deflections_cache",
            "_lc_summary", "_reaction_summary",
        ):
            try:
                setattr(bridge, attr, None)
            except Exception:
                pass

    # 3. Reclaim immediately so memory does not creep across the 200 designs.
    gc.collect()


def _deck_passes(bridge: PlateGirderBridge) -> bool:
    """Deck slab: run the real deck designer; passes iff every ur_* <= 1.0."""
    bridge.output_dict = {}                       # design_deck_slab writes here
    result = bridge.design_deck_slab()            # deckdesign.py — full IRC deck design
    urs = [v for k, v in result.items()
           if k.startswith("ur_") and isinstance(v, (int, float))]
    return bool(urs) and all(u <= 1.0 for u in urs)


def candidate_is_feasible(cand: Candidate, cfg: OptiConfig) -> bool:
    """
    Decide feasibility with cheapest-first short-circuit gates, so a hopeless
    candidate is rejected long before the (expensive) grillage analysis runs:

      GATE 0  analytical girder pre-screen (µs, no bridge)   — _girder_prescreen
      GATE 1  deck-slab design (analysis-free, ~20 ms)       — _deck_passes
      —— full ANALYSER (the costly OpenSees solves) ——
      GATE 2  IRC 22:2015 girder DCR                         — _run_dcr_checks

    The candidate is feasible only if it clears every gate. CAD / transverse
    design are skipped (not feasibility gates). The weight is computed by the
    caller only for feasible designs.
    """
    # GATE 0 — microsecond analytical pre-screen; no bridge built at all.
    if not _girder_prescreen(cand, cfg):
        return False

    bridge = PlateGirderBridge()
    try:
        bridge.set_input(candidate_input_dict(cand, cfg))

        # Cheap pre-analysis stages (build inputs + layout only).
        bridge._resolve_optimized_bounds_to_mm()   # no-op in Custom mode
        bridge._convert_girder_dims_mm_to_m()
        bridge._validate_inputs()
        bridge._solve_bridge_layout()

        # GATE 1 — DECK (analysis-free). design_deck_slab reads only input_dict,
        # so it runs here, before the grillage. _deck_passes clobbers output_dict;
        # save/restore it so the later analysis sees exactly the post-layout state.
        saved_output_dict = bridge.output_dict
        try:
            deck_ok = _deck_passes(bridge)
        finally:
            bridge.output_dict = saved_output_dict
        if not deck_ok:
            return False

        # Full ANALYSER — stages 4..4G, exactly as design() runs them. The
        # critical-position moving-load fidelity (search vs full) is set on the
        # grillage model right after it exists, before loads are added.
        bridge._stage_grillage_setup()
        if cfg.search_positions_x is not None:
            bridge.grillage_model._search_path_positions_x = np.asarray(
                cfg.search_positions_x, dtype=float
            )
        bridge.add_dead_loads()
        bridge.add_live_loads()
        bridge.add_wind_loads()
        bridge.add_temperature_load()
        bridge.add_seismic_loads()
        bridge._stage_load_combinations()
        dataset = bridge._reanalyze_with_dedup()
        dataset = bridge.create_envelope_load_case(dataset)

        # GATE 2 — IRC 22:2015 girder DCR. (Deck already passed at Gate 1 and the
        # inputs are unchanged since, so it is NOT re-run here.)
        bridge._run_dcr_checks(dataset)
        engine = getattr(bridge, "_dcr_engine", None)
        return not (engine is None or engine.overall_status() == "FAIL")
    finally:
        # This design's details have now been read (DCR status + deck URs), so
        # clear it from memory immediately — before the next candidate is built.
        reset_design(bridge)


# ------------------------------------------------------------------------------
#  Multiprocessing start method (cross-platform)
# ------------------------------------------------------------------------------

def default_start_method() -> str:
    """
    Best multiprocessing start method for the current OS.

    - Linux / macOS : "forkserver" — workers fork from a clean helper process,
      never inheriting the parent's non-fork-safe native state (Qt, OpenSees).
    - Windows        : "spawn" — the only method available; each worker is a
      fresh interpreter.

    This is chosen for the *current* process. It is safe to call from the
    isolated optimisation runner (a clean, Qt-free process), where "spawn"
    re-imports only the runner module — not the GUI app's ``__main__``.
    """
    import multiprocessing as _mp
    methods = _mp.get_all_start_methods()
    if "forkserver" in methods:
        return "forkserver"
    return "spawn"


# ------------------------------------------------------------------------------
#  Worker side: one global config per process, one picklable fitness function
# ------------------------------------------------------------------------------

_CFG: Optional[OptiConfig] = None
_EVENT_Q = None   # live per-core activity queue (set by _init_worker); may be None


def _init_worker(cfg: OptiConfig, event_q=None) -> None:
    """Pool initializer: install the run config and silence pipeline output."""
    global _CFG, _EVENT_Q
    _CFG = cfg
    _EVENT_Q = event_q
    # The design pipeline is chatty (stdout) and emits many UserWarnings
    # (no footpath/railing/median) for every candidate. Silence both so they
    # don't flood the parent's terminal during the parallel search.
    import warnings
    sys.stdout = _NullWriter()
    sys.stderr = _NullWriter()
    warnings.filterwarnings("ignore")


def _emit_core_event(ev: dict) -> None:
    """Best-effort push of a live 'which core is on which design' event."""
    q = _EVENT_Q
    if q is None:
        return
    try:
        q.put(ev)
    except Exception:
        pass


def _fitness(x: np.ndarray) -> float:
    """
    Picklable fitness used by every worker. Returns the candidate's weight if it
    is feasible, ``+inf`` otherwise (so infeasible candidates always lose greedy
    selection). Any exception in the heavy pipeline is treated as infeasible.

    Memory note: each candidate builds a PlateGirderBridge + grillage analysis,
    producing large objects (xarray result datasets, etc.). ``candidate_is_feasible``
    already frees the design via ``reset_design`` the moment its details are read.
    The ``finally`` here is just a backstop ``reset_design(None)`` — it wipes the
    OpenSees global domain and forces a gc pass even if the candidate blew up
    before a bridge was built — so memory stays flat across the whole 200-design
    run.
    """
    import os
    import time

    cfg = _CFG
    if cfg is None:
        raise RuntimeError("_fitness called before _init_worker installed OptiConfig")

    pid = os.getpid()
    t0  = time.time()
    try:
        cand = normalize_candidate(np.asarray(x, dtype=float), cfg)
        # Tell the live monitor this core has STARTED this design.
        _emit_core_event({
            "ev": "start", "pid": pid,
            "n": cand.n, "t_slab": cand.t_slab, "D": cand.D,
            "bf": cand.bf, "tf": cand.tf, "tw": cand.tw,
        })
        feasible = candidate_is_feasible(cand, cfg)
        weight   = candidate_weight(cand, cfg) if feasible else None
        _emit_core_event({
            "ev": "done", "pid": pid, "feasible": bool(feasible),
            "weight": weight, "secs": round(time.time() - t0, 1),
        })
        return weight if feasible else math.inf
    except Exception:
        _emit_core_event({
            "ev": "done", "pid": pid, "feasible": False,
            "weight": None, "secs": round(time.time() - t0, 1),
        })
        return math.inf
    finally:
        # Backstop: candidate_is_feasible already reset its bridge; this covers
        # the path where it never got that far.
        reset_design(None)


# ------------------------------------------------------------------------------
#  Bounds + warm-start (parent process only — closure over cfg is fine here)
# ------------------------------------------------------------------------------

def _make_bounds(cfg: OptiConfig):
    """Return a bounds_func(x) -> (n_dims, 2) array. Called only in the parent."""
    span  = cfg.span_m
    width = cfg.deck_width_m

    def bounds(x: np.ndarray) -> np.ndarray:
        D    = x[3]
        D_lo = span * 1000.0 / 25.0
        D_hi = span * 1000.0 / 15.0
        return np.array([
            [2,          max(2.0, math.floor(width))],   # n
            [1.0,        width],                          # s   (m)
            [150.0,      400.0],                          # t_slab (mm)
            [D_lo,       D_hi],                           # D   (mm)
            [0.20 * D,   0.40 * D],                       # bf  (mm)
            [6.0,        100.0],                          # tf  (mm)
            [6.0,        40.0],                           # tw  (mm)
        ])

    return bounds


def initial_guess(cfg: OptiConfig) -> np.ndarray:
    """DDCL empirical warm-start point seeded into population slot 0."""
    span  = cfg.span_m
    width = cfg.deck_width_m

    D   = _ceil10(span * 1000.0 / 18.0)     # typical plate-girder depth/span
    bf  = _round5(0.3 * D)
    tf  = _ceiling_plate(bf / 24.0)
    dw  = D - 2.0 * tf
    tw  = _ceiling_plate(max(dw / 200.0, 6.0))

    n       = 4
    spacing = clamp(width / 4.0, 1.0, width)
    t_slab  = 150.0
    return np.array([n, spacing, t_slab, D, bf, tf, tw], dtype=float)


# ------------------------------------------------------------------------------
#  Public entry point
# ------------------------------------------------------------------------------

def optimize_parallel(
    base_input_dict : dict,
    steel_density   : float          = 78.5,
    concrete_density: float          = 25.0,
    pop_size        : int            = 50,
    generations     : int            = 300,
    tol             : float          = 1e-1,
    seed            : int            = 42,
    max_workers     : Optional[int]  = None,
    build_cad       : bool           = False,
    on_candidate    : "Optional[callable]" = None,
    on_core_event   : "Optional[callable]" = None,
    start_method    : "Optional[str]" = None,
    max_tasks_per_child : Optional[int] = 1,
) -> PlateGirderBridge:
    """
    Optimise the plate-girder superstructure for minimum weight, evaluating the
    DE population in parallel across CPU cores.

    Parameters
    ----------
    base_input_dict : a fully solved bridge input_dict (post
                      ``solve_extend_basic_input_dict``) carrying span, overall
                      width, materials, loading, etc. Candidates override only
                      the layout + girder-section keys.
    build_cad       : if True, run the full ``design()`` (incl. CAD) on the
                      winning design before returning.
    on_candidate    : optional UI hook called once per evaluated candidate with
                      a single info dict:
                        {done, total, gen, feasible, weight_kN, components,
                         best_kN, vector}
                      ``done``/``total`` give the "x/50" progress (total ==
                      pop_size); ``components`` is the per-component weight
                      breakdown for feasible candidates (else None).
    on_core_event   : optional live "which core is on which design" hook. Called
                      (from a background drainer thread) with the raw worker
                      events ``{"ev": "start"|"done", "pid", ...}`` so a UI can
                      show a per-core monitor. Each worker emits one ``start`` and
                      one ``done`` per design it evaluates.

    Returns
    -------
    PlateGirderBridge built from the best feasible design vector. Raises
    ``RuntimeError`` if no feasible candidate was found.
    """
    # The SEARCH runs at reduced moving-load fidelity (critical positions only);
    # the winning design is re-validated at FULL fidelity below.
    span_m = float(base_input_dict[KEY_SPAN])
    search_positions = critical_path_positions_x(span_m, max_len=25.0)
    cfg = OptiConfig.from_input_dict(
        base_input_dict, steel_density, concrete_density,
        search_positions_x=search_positions,
    )
    if start_method is None:
        start_method = default_start_method()   # forkserver on POSIX, spawn on Windows

    # Live per-core monitor plumbing. Workers push start/done events onto a
    # Manager queue (picklable across any start method); a background thread here
    # drains it and forwards to on_core_event. Only set up when a UI asked for it.
    import threading
    _mgr      = None
    _event_q  = None
    _drainer  = None
    _stop_drn = threading.Event()
    if on_core_event is not None:
        import multiprocessing as _mp
        _mgr     = _mp.Manager()
        _event_q = _mgr.Queue()

        def _drain_events():
            while not _stop_drn.is_set():
                try:
                    ev = _event_q.get(timeout=0.2)
                except Exception:
                    continue
                if ev is None:
                    break
                try:
                    on_core_event(ev)
                except Exception:
                    pass
            # flush anything still queued at shutdown
            while True:
                try:
                    ev = _event_q.get_nowait()
                except Exception:
                    break
                if ev is None:
                    continue
                try:
                    on_core_event(ev)
                except Exception:
                    pass

        _drainer = threading.Thread(target=_drain_events, daemon=True)
        _drainer.start()

    # Translate the engine's low-level progress into a UI-friendly info dict,
    # tracking the running best so the loader can show it live. The bar is made
    # monotonic over the whole run (initial population + every generation) so it
    # fills to 100% instead of resetting each generation.
    best_so_far = {"kN": math.inf}
    total_batches = generations + 1   # batch 0 = initial population
    records: List[dict] = []          # one row per evaluated design, for the report tab

    def _engine_progress(done, total, gen, vector, fitness):
        feasible = math.isfinite(fitness)
        if feasible and fitness < best_so_far["kN"]:
            best_so_far["kN"] = fitness

        # Always recover the candidate's manufacturable dims + weight breakdown
        # so the report shows the actual design that was evaluated (feasible or
        # not). For infeasible candidates the weight is still meaningful.
        cand       = normalize_candidate(np.asarray(vector, dtype=float), cfg)
        components = candidate_weight_components(cand, cfg)

        record = {
            "index":      len(records) + 1,
            "gen":        gen,
            "round":      "Initial" if gen == 0 else f"Gen {gen}",
            "feasible":   feasible,
            "status":     "PASS" if feasible else "FAIL",
            # raw DE vector — lets the winner be rebuilt & re-validated at full
            # fidelity, and feasible runners-up serve as fallbacks.
            "vector":     np.asarray(vector, dtype=float).tolist(),
            # design values actually used (post-snapping)
            "n":          cand.n,
            "spacing_m":  round(cand.s, 3),
            "t_slab_mm":  cand.t_slab,
            "D_mm":       cand.D,
            "bf_mm":      cand.bf,
            "tf_mm":      cand.tf,
            "tw_mm":      cand.tw,
            "dw_mm":      cand.dw,
            # weights
            "steel_kN":   round(components["steel_girders_kN"], 1),
            "deck_kN":    round(components["deck_concrete_kN"], 1),
            "total_kN":   round(components["total_kN"], 1),
        }
        records.append(record)

        if on_candidate is None:
            return
        overall_done  = gen * total + done
        overall_total = total_batches * total
        on_candidate({
            "done":          done,
            "total":         total,
            "gen":           gen,
            "overall_done":  overall_done,
            "overall_total": overall_total,
            "feasible":      feasible,
            "weight_kN":     fitness if feasible else None,
            "components":    components,
            "best_kN":       best_so_far["kN"] if math.isfinite(best_so_far["kN"]) else None,
            "vector":        np.asarray(vector, dtype=float),
            "record":        record,
        })

    try:
        result: OptimisationResult = ParallelOptimizer.run(
            fitness_func    = _fitness,
            bounds_func     = _make_bounds(cfg),
            initial_guess   = initial_guess(cfg),
            tolerance       = tol,
            pop_size        = pop_size,
            generations     = generations,
            seed            = seed,
            max_workers     = max_workers,
            worker_init     = _init_worker,
            worker_initargs = (cfg, _event_q),
            progress_cb     = _engine_progress,
            # OS-aware: forkserver on POSIX, spawn on Windows (see default_start_method).
            # Intended to run inside the isolated `run_optimization` process, where
            # neither method touches the GUI app's __main__.
            start_method    = start_method,
            # Recycle each worker after one design so its memory (incl. C-level
            # OpenSees / ospgrillage state) is returned to the OS per design, not
            # only when the whole run ends.
            max_tasks_per_child = max_tasks_per_child,
        )
    finally:
        # Tear down the live-monitor drainer + manager.
        _stop_drn.set()
        if _event_q is not None:
            try: _event_q.put(None)
            except Exception: pass
        if _drainer is not None:
            _drainer.join(timeout=2.0)
        if _mgr is not None:
            try: _mgr.shutdown()
            except Exception: pass

    if not result.feasible:
        raise RuntimeError(
            "Optimisation found no feasible design — relax bounds, loosen tol, "
            "or check the base input_dict / loading."
        )

    print("Convergence achieved" if result.converged else "Convergence not achieved")
    print(f"Best superstructure weight: {result.best_fitness:.1f} kN")

    # --- Full-fidelity re-validation of the winner ----------------------------
    # The search ranked candidates with a reduced (critical-position) moving load,
    # so its winner is only *approximately* feasible. Re-check candidates best-first
    # at FULL fidelity (50-step sweep, all gates); the first that truly passes is
    # the returned design. Feasible runners-up (ascending weight) are fallbacks.
    cfg_full = replace(cfg, search_positions_x=None)
    candidate_vectors = [np.asarray(result.best_vector, dtype=float)]
    for r in sorted(
        (r for r in records if r.get("feasible") and r.get("vector") is not None),
        key=lambda r: r["total_kN"],
    ):
        candidate_vectors.append(np.asarray(r["vector"], dtype=float))

    winning_vector = None
    for vec in candidate_vectors:
        try:
            if candidate_is_feasible(normalize_candidate(vec, cfg_full), cfg_full):
                winning_vector = vec
                break
        except Exception:
            continue

    if winning_vector is None:
        raise RuntimeError(
            "No candidate survived full-fidelity re-validation — every reduced-"
            "fidelity search winner failed the full 50-step check. Relax bounds "
            "or widen the search-phase critical positions."
        )

    # Rebuild the winning bridge in the parent process from its design vector
    # (full-fidelity cfg, so a later design()/CAD build uses the 50-step sweep).
    cand   = normalize_candidate(winning_vector, cfg_full)
    bridge = PlateGirderBridge()
    bridge.set_input(candidate_input_dict(cand, cfg_full))

    # Expose the full run for the report tab: every evaluated design (feasible or
    # not) with its values + weights, plus which one won.
    best_total = round(candidate_weight_components(cand, cfg)["total_kN"], 1)
    for r in records:
        r["is_best"] = (
            r["feasible"]
            and r["n"] == cand.n and r["D_mm"] == cand.D
            and r["bf_mm"] == cand.bf and r["tf_mm"] == cand.tf
            and r["tw_mm"] == cand.tw and r["t_slab_mm"] == cand.t_slab
            and abs(r["total_kN"] - best_total) < 1e-6
        )
    bridge.optimization_records = records
    bridge.optimization_summary = {
        "evaluated":   len(records),
        "feasible":    sum(1 for r in records if r["feasible"]),
        "infeasible":  sum(1 for r in records if not r["feasible"]),
        "best_total_kN": best_total,
        "converged":   result.converged,
    }

    if build_cad:
        bridge.design()

    return bridge
