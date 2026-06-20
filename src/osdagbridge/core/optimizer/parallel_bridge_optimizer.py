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
  * Feasibility is evaluated through the **real, maintained** bridge pipeline
    (the same staged methods ``PlateGirderBridge.design()`` runs) up to the
    IRC 22:2015 DCR checks - CAD / deck / transverse stages are skipped because
    they are irrelevant to feasibility and expensive. CAD is generated once, in
    the parent process, for the winning design only.

Usage
-----
    from osdagbridge.core.optimizer.parallel_bridge_optimizer import optimize_parallel

    bridge = optimize_parallel(base_input_dict)   # a fully solved input_dict
    bridge.design()                               # full pipeline + CAD on the winner
"""

from __future__ import annotations

import copy
import math
import sys
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from osdagbridge.core.bridge_types.plate_girder.plategirderbridge import PlateGirderBridge
from osdagbridge.core.bridge_types.plate_girder.designer import SteelSection
from osdagbridge.core.utils.common import (
    KEY_SPAN,
    KEY_TS_OVERALL_WIDTH,
    KEY_TS_NO_OF_GIRDERS,
    KEY_TS_GIRDER_SPACING,
    KEY_TS_DECK_OVERHANG,
    KEY_TS_DECK_THICKNESS,
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

    @classmethod
    def from_input_dict(
        cls,
        base_input_dict : dict,
        steel_density   : float = 78.5,
        concrete_density: float = 25.0,
    ) -> "OptiConfig":
        return cls(
            base_input_dict  = copy.deepcopy(base_input_dict),
            span_m           = float(base_input_dict[KEY_SPAN]),
            deck_width_m     = float(base_input_dict[KEY_TS_OVERALL_WIDTH]),
            steel_density    = steel_density,
            concrete_density = concrete_density,
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
    t_slab   = _round5(clamp(t_slab, 150.0, 250.0))

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
    for base_key, val in dims_mm.items():
        d[base_key] = val
        for gi in range(cand.n):
            d[f"{base_key}.G{gi + 1}.M1"] = val

    return d


# ------------------------------------------------------------------------------
#  Feasibility via the real, maintained bridge pipeline (up to DCR only)
# ------------------------------------------------------------------------------

class _NullWriter:
    def write(self, _): pass
    def flush(self): pass


def candidate_is_feasible(cand: Candidate, cfg: OptiConfig) -> bool:
    """
    Run the same staged pipeline ``PlateGirderBridge.design()`` runs, up to and
    including the IRC 22:2015 DCR checks, then report whether the controlling
    girder passes. Deck / transverse / CAD stages are intentionally skipped.
    """
    bridge = PlateGirderBridge()
    bridge.set_input(candidate_input_dict(cand, cfg))

    # Pre-stage unit handling + stages 1..4G, mirroring design().
    bridge._resolve_optimized_bounds_to_mm()   # no-op in Custom mode
    bridge._convert_girder_dims_mm_to_m()
    bridge._validate_inputs()
    bridge._solve_bridge_layout()
    bridge._stage_grillage_setup()
    bridge.add_dead_loads()
    bridge.add_live_loads()
    bridge.add_wind_loads()
    bridge.add_temperature_load()
    bridge.add_seismic_loads()
    bridge._stage_load_combinations()
    dataset = bridge._reanalyze_with_dedup()
    dataset = bridge.create_envelope_load_case(dataset)

    # Stage 5: DCR checks. Populates bridge._dcr_engine.
    bridge._run_dcr_checks(dataset)

    engine = getattr(bridge, "_dcr_engine", None)
    if engine is None:
        return False
    return engine.overall_status() != "FAIL"


# ------------------------------------------------------------------------------
#  Worker side: one global config per process, one picklable fitness function
# ------------------------------------------------------------------------------

_CFG: Optional[OptiConfig] = None


def _init_worker(cfg: OptiConfig) -> None:
    """Pool initializer: install the run config and silence pipeline output."""
    global _CFG
    _CFG = cfg
    # The design pipeline is chatty (stdout) and emits many UserWarnings
    # (no footpath/railing/median) for every candidate. Silence both so they
    # don't flood the parent's terminal during the parallel search.
    import warnings
    sys.stdout = _NullWriter()
    sys.stderr = _NullWriter()
    warnings.filterwarnings("ignore")


def _fitness(x: np.ndarray) -> float:
    """
    Picklable fitness used by every worker. Returns the candidate's weight if it
    is feasible, ``+inf`` otherwise (so infeasible candidates always lose greedy
    selection). Any exception in the heavy pipeline is treated as infeasible.
    """
    cfg = _CFG
    if cfg is None:
        raise RuntimeError("_fitness called before _init_worker installed OptiConfig")
    try:
        cand = normalize_candidate(np.asarray(x, dtype=float), cfg)
        if not candidate_is_feasible(cand, cfg):
            return math.inf
        return candidate_weight(cand, cfg)
    except Exception:
        return math.inf


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
            [150.0,      250.0],                          # t_slab (mm)
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
    start_method    : str            = "forkserver",
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

    Returns
    -------
    PlateGirderBridge built from the best feasible design vector. Raises
    ``RuntimeError`` if no feasible candidate was found.
    """
    cfg = OptiConfig.from_input_dict(base_input_dict, steel_density, concrete_density)

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

    result: OptimisationResult = ParallelOptimizer.run(
        fitness_func    = _fitness,
        bounds_func     = _make_bounds(cfg),
        initial_guess   = initial_guess(cfg),
        tol             = tol,
        pop_size        = pop_size,
        generations     = generations,
        seed            = seed,
        max_workers     = max_workers,
        worker_init     = _init_worker,
        worker_initargs = (cfg,),
        progress_cb     = _engine_progress,
        # "forkserver": workers fork from a clean helper process, NOT from the
        # live Qt/OpenSees parent (which is not fork-safe and crashes children).
        # Unlike "spawn" it does not re-import the app's __main__ module.
        start_method    = start_method,
    )

    if not result.feasible:
        raise RuntimeError(
            "Optimisation found no feasible design — relax bounds, loosen tol, "
            "or check the base input_dict / loading."
        )

    print("Convergence achieved" if result.converged else "Convergence not achieved")
    print(f"Best superstructure weight: {result.best_fitness:.1f} kN")

    # Rebuild the winning bridge in the parent process from its design vector.
    cand   = normalize_candidate(result.best_vector, cfg)
    bridge = PlateGirderBridge()
    bridge.set_input(candidate_input_dict(cand, cfg))

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
