"""
collect_optimization_dataset.py
================================
Standalone dataset collector for the plate-girder bridge optimiser.

What it does
------------
For each span in 20.0, 20.1, 20.2, 20.3, 20.4, 20.5 m (carriageway width fixed
at 4.25 m) it builds a full bridge ``input_dict`` exactly the way the GUI does
(``BASIC_INPUT_DICT`` + ``solve_extend_basic_input_dict``) and runs the parallel
Differential-Evolution weight optimisation (``optimize_parallel``) with
``pop_size=50`` and ``generations=3`` -> 50 x (3 + 1) = **200 evaluated designs**
per span.

Every evaluated design is one row: the fixed inputs (span, carriageway width,
median, footpath, skew) plus everything DE varies per candidate (number of
girders, spacing, slab thickness, girder depth, flange width/thickness, web
thickness) and the resulting weights and feasibility.

The growing table is pickled (a pandas DataFrame) to
``ResourceFiles/optimization_dataset.pkl`` after every span, so a crash never
loses already-collected data. Load it back with ``pandas.read_pickle(...)``.

Run it
------
    cd OsdagBridge/src
    conda run -n osdag-env python collect_optimization_dataset.py

(Must be run from ``OsdagBridge/src`` with the ``osdag-env`` interpreter so the
``osdagbridge`` package and OpenSees / ospgrillage resolve.)
"""

from __future__ import annotations

import copy
import os
import time

import pandas as pd

from osdagbridge.core.bridge_types.plate_girder.defaults import (
    BASIC_INPUT_DICT,
    solve_extend_basic_input_dict,
)
from osdagbridge.core.optimizer.parallel_bridge_optimizer import optimize_parallel
from osdagbridge.core.utils.common import (
    KEY_SPAN,
    KEY_CARRIAGEWAY_WIDTH,
    KEY_INCLUDE_MEDIAN,
    KEY_FOOTPATH,
    KEY_SKEW_ANGLE,
)


# ------------------------------------------------------------------------------
#  Sweep configuration
# ------------------------------------------------------------------------------

SPAN_START   = 20.0
SPAN_END     = 30.0      # inclusive
SPAN_STEP    = 0.1
CARRIAGEWAY_WIDTH = 4.25  # m — fixed for the whole sweep

# Fixed layout inputs (recorded as columns alongside the DE variables).
INCLUDE_MEDIAN = "No"
FOOTPATH       = "None"
SKEW_ANGLE     = 0.0

# DE run size: 50 x (3 + 1) = 200 designs per span.
POP_SIZE    = 50
GENERATIONS = 3

# Where the dataset lands.
_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR  = os.path.join(_THIS_DIR, "ResourceFiles")
OUTPUT_PKL  = os.path.join(OUTPUT_DIR, "optimization_dataset.pkl")


def _spans() -> list[float]:
    """20.0, 20.1, ... 20.5 (floating-point safe)."""
    n = int(round((SPAN_END - SPAN_START) / SPAN_STEP)) + 1
    return [round(SPAN_START + i * SPAN_STEP, 2) for i in range(n)]


def build_base_input_dict(span_m: float, width_m: float) -> dict:
    """
    Build a fully solved bridge ``input_dict`` for one (span, width), mirroring
    what the GUI does before launching the optimiser:

      1. deep-copy the package default dict,
      2. fill in the required basic inputs (span, carriageway width, skew,
         median, footpath),
      3. run ``solve_extend_basic_input_dict`` to compute the layout + all
         additional-input / loading / design-option defaults in place.
    """
    d = copy.deepcopy(BASIC_INPUT_DICT)
    d[KEY_SPAN]              = span_m
    d[KEY_CARRIAGEWAY_WIDTH] = width_m
    d[KEY_SKEW_ANGLE]        = SKEW_ANGLE
    d[KEY_INCLUDE_MEDIAN]    = INCLUDE_MEDIAN
    d[KEY_FOOTPATH]          = FOOTPATH
    # KEY_DESIGN_MODE stays "Optimized" (the default) — optimize_parallel reads
    # span / overall-width from this dict and overrides only the section keys.

    solve_extend_basic_input_dict(d)  # in-place: layout + every default
    return d


def collect_for_span(span_m: float, width_m: float) -> list[dict]:
    """
    Run one 200-design optimisation for (span, width) and return one row dict
    per evaluated design, tagged with the fixed inputs.

    Records are captured live via the ``on_candidate`` hook so they survive even
    if the optimiser raises at the end (e.g. no feasible winner).
    """
    base = build_base_input_dict(span_m, width_m)

    rows: list[dict] = []

    def _tag(record: dict) -> dict:
        r = dict(record)
        r["span_m"]               = span_m
        r["carriageway_width_m"]  = width_m
        r["include_median"]       = INCLUDE_MEDIAN
        r["footpath"]             = FOOTPATH
        r["skew_angle_deg"]       = SKEW_ANGLE
        r.pop("is_best", None)    # not needed in the dataset
        return r

    def _on_candidate(info: dict) -> None:
        rec = info.get("record")
        if rec is not None:
            rows.append(_tag(rec))

    best_records = None
    try:
        bridge = optimize_parallel(
            base,
            pop_size=POP_SIZE,
            generations=GENERATIONS,
            build_cad=False,          # dataset only — no CAD
            on_candidate=_on_candidate,
        )
        best_records = getattr(bridge, "optimization_records", None)
    except RuntimeError as exc:
        # No feasible design for this span — keep the rows we captured live.
        print(f"  [warn] span {span_m}: {exc}")

    # Prefer the optimiser's final records (guaranteed complete); fall back to
    # the live-captured rows.
    if best_records:
        rows = [_tag(r) for r in best_records]

    return rows


# Column order for the saved dataset: fixed inputs first, then the DE variables,
# then weights / status.
_COLUMNS = [
    "span_m", "carriageway_width_m", "include_median", "footpath", "skew_angle_deg",
    "index", "round", "gen",
    "n", "spacing_m", "t_slab_mm", "D_mm", "bf_mm", "tf_mm", "tw_mm", "dw_mm",
    "steel_kN", "deck_kN", "total_kN",
    "feasible", "status",
]


def _save(rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    # Keep known columns in a stable order; append any extras at the end.
    ordered = [c for c in _COLUMNS if c in df.columns]
    extras  = [c for c in df.columns if c not in ordered]
    df = df[ordered + extras]
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    df.to_pickle(OUTPUT_PKL)   # pandas DataFrame pickle; load with pd.read_pickle


def main() -> None:
    spans = _spans()
    print(f"Collecting optimisation dataset for spans {spans} "
          f"(width fixed {CARRIAGEWAY_WIDTH} m), "
          f"{POP_SIZE} x {GENERATIONS + 1} = {POP_SIZE * (GENERATIONS + 1)} designs each.")
    print(f"Output -> {OUTPUT_PKL}\n")

    all_rows: list[dict] = []
    t0 = time.time()
    for i, span in enumerate(spans, start=1):
        print(f"[{i}/{len(spans)}] span = {span} m ...")
        t_span = time.time()
        span_rows = collect_for_span(span, CARRIAGEWAY_WIDTH)
        all_rows.extend(span_rows)
        _save(all_rows)   # checkpoint after every span
        print(f"    -> {len(span_rows)} designs collected "
              f"({len(all_rows)} total) in {time.time() - t_span:.1f}s; "
              f"saved to {os.path.relpath(OUTPUT_PKL, _THIS_DIR)}")

    print(f"\nDone. {len(all_rows)} rows across {len(spans)} spans "
          f"in {time.time() - t0:.1f}s -> {OUTPUT_PKL}")


if __name__ == "__main__":
    main()
