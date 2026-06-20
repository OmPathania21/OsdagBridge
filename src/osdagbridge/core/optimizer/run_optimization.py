"""
run_optimization.py — isolated optimisation runner (cross-platform)
===================================================================

Launched by the GUI as a **separate process**:

    python -m osdagbridge.core.optimizer.run_optimization <in_pickle> <out_pickle>

Why a separate process
----------------------
The GUI process has Qt (and a live OpenSees domain) loaded, which is NOT safe to
fork and, on Windows, would force ``spawn`` to re-import the GUI's ``__main__``
(re-running ``create_sqlite`` and the whole UI in every worker). Running the
optimisation here instead means:

  * This process is **clean** (no Qt) — so its internal pool can use ``fork`` /
    ``forkserver`` on Linux/macOS and ``spawn`` on Windows safely.
  * On Windows, ``spawn`` re-imports only THIS module (guarded, side-effect free),
    never the GUI app — so the app's ``__main__`` is left completely untouched.

Protocol
--------
Input  : a pickle file holding ``{"base_input_dict": dict, "params": dict}``.
Output : progress is streamed to stdout, one JSON object per line, each prefixed
         with the sentinel ``@@OPT@@`` so the parent can pick them out of any
         other noise. The final result (winning ``input_dict`` + records +
         summary) is pickled to ``<out_pickle>``.

This module must stay import-light and free of GUI imports.
"""

from __future__ import annotations

import json
import pickle
import sys

SENTINEL = "@@OPT@@"


def _emit(obj: dict) -> None:
    """Write one sentinel-prefixed JSON line to stdout and flush immediately."""
    try:
        sys.__stdout__.write(SENTINEL + json.dumps(obj) + "\n")
        sys.__stdout__.flush()
    except Exception:
        pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        _emit({"type": "error", "message": "usage: run_optimization <in> <out>"})
        return 2
    in_path, out_path = argv[0], argv[1]

    try:
        with open(in_path, "rb") as fh:
            payload = pickle.load(fh)
        base_input_dict = payload["base_input_dict"]
        params = dict(payload.get("params") or {})

        from osdagbridge.core.optimizer.parallel_bridge_optimizer import optimize_parallel

        _emit({"type": "start", "pop_size": params.get("pop_size"),
               "generations": params.get("generations")})

        def _on_candidate(info: dict) -> None:
            # Forward only JSON-serialisable fields (drop numpy vector etc.).
            comp = info.get("components") or {}
            _emit({
                "type":          "progress",
                "done":          int(info["done"]),
                "total":         int(info["total"]),
                "gen":           int(info["gen"]),
                "overall_done":  int(info["overall_done"]),
                "overall_total": int(info["overall_total"]),
                "feasible":      bool(info["feasible"]),
                "weight_kN":     info.get("weight_kN"),
                "best_kN":       info.get("best_kN"),
                "steel_kN":      comp.get("steel_girders_kN"),
                "deck_kN":       comp.get("deck_concrete_kN"),
            })

        # build_cad stays False here: CAD is built once, in the GUI process, on
        # the winning design (pythonocc + rendering belong with the GUI).
        params.pop("build_cad", None)
        params.pop("on_candidate", None)
        bridge = optimize_parallel(
            base_input_dict,
            on_candidate=_on_candidate,
            build_cad=False,
            **params,
        )

        result = {
            "input_dict": bridge.input_dict,
            "records":    getattr(bridge, "optimization_records", []),
            "summary":    getattr(bridge, "optimization_summary", {}),
        }
        with open(out_path, "wb") as fh:
            pickle.dump(result, fh)

        _emit({"type": "done"})
        return 0

    except Exception as exc:
        import traceback
        _emit({"type": "error", "message": f"{type(exc).__name__}: {exc}",
               "traceback": traceback.format_exc()})
        return 1


if __name__ == "__main__":
    sys.exit(main())
